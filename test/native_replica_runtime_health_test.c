/* Execute the production bulk readers/writer against bounded native-shaped
 * memory. Compare every written byte and count the memory-read calls. */
#include <assert.h>
#include <stdio.h>
#include "../native/replica_runtime_health.h"
static unsigned char zones[2][8],clusters[2][0x180],blocks[4096][0x190];
static uintptr_t vectors[2][4096];
static unsigned reads,writes,maximum_calls;static uintptr_t unreadable,active_cluster;
static bool inside(const void *source,size_t size,const void *base,size_t length){
    uintptr_t at=(uintptr_t)source,first=(uintptr_t)base;
    return at>=first && at-first<=length && size<=length-(at-first);
}
static bool fake_read(const void *source,void *out,size_t size){
    reads++;
    if((uintptr_t)source==unreadable ||
       (!inside(source,size,clusters,sizeof(clusters)) && !inside(source,size,blocks,sizeof(blocks)) &&
        !inside(source,size,vectors,sizeof(vectors))))return false;
    memcpy(out,source,size);return true;
}
static void fake_write(void *destination,const void *source,size_t size){
    assert(inside(destination,size,blocks,sizeof(blocks)));writes++;memcpy(destination,source,size);
}
static float fake_maximum(void *block){
    uintptr_t owner;float maximum;maximum_calls++;
    assert(inside(block,0x190,blocks,sizeof(blocks)));
    memcpy(&owner,(char*)block+0xb8,8);assert(owner==active_cluster);
    memcpy(&maximum,(char*)block+0xf8,4);return maximum;
}
static void pointer(void *base,size_t offset,uintptr_t value){memcpy((char*)base+offset,&value,8);}
static void number(void *base,size_t offset,unsigned value){memcpy((char*)base+offset,&value,4);}
static void scalar(void *base,size_t offset,float value){memcpy((char*)base+offset,&value,4);}
static float get(void *base,size_t offset){float value;memcpy(&value,(char*)base+offset,4);return value;}
static void vector(int cluster,size_t count){
    pointer(clusters[cluster],0xf0,(uintptr_t)vectors[cluster]);pointer(clusters[cluster],0xf8,(uintptr_t)(vectors[cluster]+count));
}
static void fixture(void){
    memset(clusters,0,sizeof(clusters));memset(blocks,0,sizeof(blocks));memset(vectors,0,sizeof(vectors));
    for(int i=0;i<2;i++){
        pointer(clusters[i],8,(uintptr_t)zones[0]);
        double position[2]={12.25+i,-31.125-i};memcpy(clusters[i]+0x30,position,sizeof(position));
    }
    pointer(clusters[1],0x178,(uintptr_t)clusters[0]);
    for(int i=0;i<4096;i++){
        pointer(blocks[i],0xb8,(uintptr_t)clusters[0]);number(blocks[i],0x30,(unsigned)i+1);
        scalar(blocks[i],0x38,4.5f);scalar(blocks[i],0x48,.125f);scalar(blocks[i],0x4c,17.5f);scalar(blocks[i],0xf8,100.0f+i);
        uint64_t features=(i==0?0x402:i==1?0x90:0);memcpy(blocks[i]+0x40,&features,8);
        vectors[0][i]=(uintptr_t)blocks[i];
    }
    vector(0,3);vector(1,0);reads=writes=maximum_calls=0;unreadable=0;active_cluster=(uintptr_t)clusters[0];
}
int main(void){
    fixture();struct ReplicaRuntimeCluster cluster;
    assert(replica_runtime_cluster_read((uintptr_t)clusters[0],(uintptr_t)zones[0],&cluster,fake_read));
    assert(cluster.parent==0 && cluster.position[0]==12.25 && cluster.position[1]==-31.125 && reads==1);
    assert(replica_runtime_cluster_read((uintptr_t)clusters[1],(uintptr_t)zones[0],&cluster,fake_read));
    assert(cluster.parent==(uintptr_t)clusters[0]);
    assert(!replica_runtime_cluster_read((uintptr_t)clusters[0],(uintptr_t)zones[1],&cluster,fake_read));
    uintptr_t copied[4096];size_t length;reads=0;
    assert(replica_runtime_blocks_copy((uintptr_t)clusters[0],copied,&length,fake_read) && length==3 && reads==2);
    assert(!memcmp(copied,vectors[0],3*sizeof(*copied)));
    unsigned minimum=0;reads=0;
    assert(replica_runtime_minimum_ident((uintptr_t)clusters[0],&minimum,fake_read) && minimum==1 && reads==5);
    number(blocks[0],0x30,0);
    assert(replica_runtime_minimum_ident((uintptr_t)clusters[0],&minimum,fake_read) && minimum==2);
    fixture();struct ReplicaRuntimeBlock current;reads=0;
    assert(replica_runtime_block_read((uintptr_t)blocks[0],(uintptr_t)clusters[0],&current,fake_read) && reads==1);
    assert(current.ident==1 && current.health==17.5f && current.growth==.125f && current.lifetime==4.5f && current.features==0x402);
    struct ReplicaHealth exported;
    replica_runtime_source_health(current,&exported);
    assert(exported.ident==1 && exported.health==17.5f && exported.growth==.125f && exported.lifetime==4.5f);
    for(int kind=0;kind<3;kind++){
        current.health=kind==0?-17.5f:kind==1?-1.0f:NAN;current.growth=-1;current.lifetime=-1;
        replica_runtime_source_health(current,&exported);
        assert(exported.health==0 && exported.growth==-1 && exported.lifetime==-1);
    }
    /* Exact legacy scalar semantics: healthy sentinel uses native maximum,
     * growth/lifetime sentinels preserve local fields, explicit zero stays0. */
    fixture();unsigned char before[3][0x190];memcpy(before,blocks,sizeof(before));
    struct ReplicaHealth rows[3]={{1,-1,-1,-1},{2,0,.75f,0},{3,42.125f,1,77.5f}};
    assert(replica_runtime_health_valid(rows,3));unsigned long long applied=0;
    assert(replica_runtime_health_apply((uintptr_t)clusters[0],rows,3,true,fake_read,fake_write,fake_maximum,&applied)==0);
    assert(applied==3 && maximum_calls==1 && reads==5 && writes==7);
    assert(get(blocks[0],0x4c)==100 && get(blocks[0],0x48)==.125f && get(blocks[0],0x38)==4.5f);
    for(int i=0;i<3;i++){
        float h=i==0?100:rows[i].health;memcpy(before[i]+0x4c,&h,4);
        if(rows[i].growth>=0)memcpy(before[i]+0x48,&rows[i].growth,4);
        if(rows[i].lifetime>=0)memcpy(before[i]+0x38,&rows[i].lifetime,4);
    }
    assert(!memcmp(before,blocks,sizeof(before)));
    /* Optional attached roots skip absent terminal IDs and count matched
     * health writes exactly as the old child loop did. */
    fixture();pointer(blocks[2],0xb8,(uintptr_t)clusters[1]);vectors[1][0]=(uintptr_t)blocks[2];vector(1,1);
    active_cluster=(uintptr_t)clusters[1];applied=10;
    assert(replica_runtime_health_apply((uintptr_t)clusters[1],rows,2,false,fake_read,fake_write,fake_maximum,&applied)==0 && !writes && applied==10);
    assert(replica_runtime_health_apply((uintptr_t)clusters[1],rows,3,false,fake_read,fake_write,fake_maximum,&applied)==0 && writes==3 && applied==11);
    fixture();vector(0,1);pointer(blocks[0],0xb8,(uintptr_t)clusters[1]);
    assert(replica_runtime_health_apply((uintptr_t)clusters[0],rows,3,true,fake_read,fake_write,fake_maximum,&applied)==-3 && !writes);
    assert(!replica_runtime_minimum_ident((uintptr_t)clusters[0],&minimum,fake_read));
    fixture();vector(0,1);number(blocks[0],0x30,99);
    assert(replica_runtime_health_apply((uintptr_t)clusters[0],rows,3,true,fake_read,fake_write,fake_maximum,&applied)==-4 && !writes);
    assert(replica_runtime_health_apply((uintptr_t)clusters[0],rows,3,false,fake_read,fake_write,fake_maximum,&applied)==0 && !writes);
    fixture();unreadable=(uintptr_t)blocks[0]+0x30;
    assert(replica_runtime_health_apply((uintptr_t)clusters[0],rows,3,true,fake_read,fake_write,fake_maximum,&applied)==-3 && !writes);
    fixture();unreadable=(uintptr_t)vectors[0];
    assert(!replica_runtime_blocks_copy((uintptr_t)clusters[0],copied,&length,fake_read) && !writes);
    for(int invalid=0;invalid<4;invalid++){
        fixture();uintptr_t begin=(uintptr_t)vectors[0],end=begin+8;
        if(invalid==0)end=begin-8;if(invalid==1)end=begin+3;if(invalid==2)end=begin+4097*8;
        if(invalid==3){begin=0;end=8;}
        pointer(clusters[0],0xf0,begin);pointer(clusters[0],0xf8,end);
        assert(!replica_runtime_blocks_copy((uintptr_t)clusters[0],copied,&length,fake_read) && !writes);
    }
    fixture();vector(0,0);reads=0;
    assert(replica_runtime_blocks_copy((uintptr_t)clusters[0],copied,&length,fake_read) && !length && reads==1);
    fixture();vector(0,4096);reads=0;
    assert(replica_runtime_minimum_ident((uintptr_t)clusters[0],&minimum,fake_read) && minimum==1 && reads==4098);
    assert(replica_runtime_blocks_copy((uintptr_t)clusters[0],copied,&length,fake_read) && length==4096);
    /* Invalid incoming rows cannot be used by the enclosing writer. */
    for(int invalid=0;invalid<9;invalid++){
        struct ReplicaHealth bad[3];memcpy(bad,rows,sizeof(bad));
        if(invalid==0)bad[2].ident=0;if(invalid==1)bad[2].ident=2;if(invalid==2)bad[2].health=NAN;
        if(invalid==3)bad[2].health=-2;if(invalid==4)bad[2].health=INFINITY;
        if(invalid==5)bad[2].growth=1.1f;if(invalid==6)bad[2].growth=-2;
        if(invalid==7)bad[2].lifetime=-2;if(invalid==8)bad[2].lifetime=1000001;
        assert(!replica_runtime_health_valid(bad,3));
    }
    assert(!replica_runtime_health_valid(NULL,3) && !replica_runtime_health_valid(rows,0) && !replica_runtime_health_valid(rows,65537));
    puts("bulk runtime health/source fields, exact scalar equivalence, ownership/bounds and 4096-block read budget passed");
    return 0;
}
