/* Exact-build bounded reads for the owned update transaction. No native
 * pointers or vector snapshots survive the enclosing application call. */
#ifndef REPOPULATED_REPLICA_RUNTIME_HEALTH_H
#define REPOPULATED_REPLICA_RUNTIME_HEALTH_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>
struct ReplicaHealth {unsigned ident;float health,growth,lifetime;};
_Static_assert(sizeof(struct ReplicaHealth)==16,"replica health layout");
typedef bool (*ReplicaRuntimeRead)(const void*,void*,size_t);
typedef void (*ReplicaRuntimeWrite)(void*,const void*,size_t);
typedef float (*ReplicaRuntimeMaximum)(void*);
struct ReplicaRuntimeBlock {unsigned ident;float health,growth,lifetime;uint64_t features;};
struct ReplicaRuntimeCluster {uintptr_t parent;double position[2];};
static void replica_runtime_source_health(struct ReplicaRuntimeBlock current,struct ReplicaHealth *out){
    *out=(struct ReplicaHealth){current.ident,fmaxf(0,current.health),current.growth,current.lifetime};
}
static bool replica_runtime_cluster_read(uintptr_t cluster,uintptr_t zone,struct ReplicaRuntimeCluster *out,ReplicaRuntimeRead read){
    unsigned char header[0x180];uintptr_t owner;
    if(!cluster || !zone || !out || !read || !read((void*)cluster,header,sizeof(header)))return false;
    memcpy(&owner,header+8,8);memcpy(&out->parent,header+0x178,8);memcpy(out->position,header+0x30,16);
    return owner==zone;
}
static bool replica_runtime_blocks_copy(uintptr_t cluster,uintptr_t *blocks,size_t *count,ReplicaRuntimeRead read){
    uintptr_t vector[2];
    if(!cluster || !blocks || !count || !read || !read((void*)(cluster+0xf0),vector,sizeof(vector)) ||
       vector[1]<vector[0] || (vector[1]-vector[0])%sizeof(uintptr_t) || vector[1]-vector[0]>4096*sizeof(uintptr_t))return false;
    *count=(vector[1]-vector[0])/sizeof(uintptr_t);
    return !*count || (vector[0] && read((void*)vector[0],blocks,*count*sizeof(*blocks)));
}
static bool replica_runtime_block_read(uintptr_t block,uintptr_t cluster,struct ReplicaRuntimeBlock *out,ReplicaRuntimeRead read){
    unsigned char header[0x90];uintptr_t owner;
    if(!block || !cluster || !out || !read || !read((void*)(block+0x30),header,sizeof(header)))return false;
    memcpy(&out->ident,header,4);memcpy(&out->lifetime,header+8,4);
    memcpy(&out->features,header+0x10,8);memcpy(&out->growth,header+0x18,4);
    memcpy(&out->health,header+0x1c,4);memcpy(&owner,header+0x88,8);
    return owner==cluster;
}
static bool replica_runtime_minimum_ident(uintptr_t cluster,unsigned *minimum,ReplicaRuntimeRead read){
    uintptr_t pointers[4096];size_t count;unsigned result=0;
    if(!minimum || !replica_runtime_blocks_copy(cluster,pointers,&count,read))return false;
    for(size_t i=0;i<count;i++){
        struct ReplicaRuntimeBlock block;
        if(!replica_runtime_block_read(pointers[i],cluster,&block,read))return false;
        if(block.ident && (!result || block.ident<result))result=block.ident;
    }
    *minimum=result;return true;
}
static bool replica_runtime_health_valid(const struct ReplicaHealth *rows,int count){
    if(count<1 || count>65536 || !rows)return false;
    for(int i=0;i<count;i++)if(!rows[i].ident || (i && rows[i-1].ident>=rows[i].ident) ||
       !isfinite(rows[i].health) || (rows[i].health!=-1 && (rows[i].health<0 || rows[i].health>1e9f)) ||
       !isfinite(rows[i].growth) || (rows[i].growth!=-1 && (rows[i].growth<0 || rows[i].growth>1)) ||
       !isfinite(rows[i].lifetime) || rows[i].lifetime < -1 || rows[i].lifetime>1e6f)return false;
    return true;
}
static const struct ReplicaHealth *replica_runtime_health_lookup(const struct ReplicaHealth *rows,int count,unsigned ident){
    int lo=0,hi=count;
    while(lo<hi){int mid=lo+(hi-lo)/2;if(rows[mid].ident<ident)lo=mid+1;else hi=mid;}
    return lo<count && rows[lo].ident==ident?rows+lo:NULL;
}
static int replica_runtime_health_apply(uintptr_t cluster,const struct ReplicaHealth *rows,int count,bool required,
                                       ReplicaRuntimeRead read,ReplicaRuntimeWrite write,ReplicaRuntimeMaximum maximum,
                                       unsigned long long *applied){
    uintptr_t pointers[4096];size_t length;
    if(!rows || count<1 || count>65536 || !write || !maximum ||
       !replica_runtime_blocks_copy(cluster,pointers,&length,read))return -3;
    for(size_t i=0;i<length;i++){
        uintptr_t block=pointers[i];struct ReplicaRuntimeBlock current;
        if(!replica_runtime_block_read(block,cluster,&current,read))return -3;
        const struct ReplicaHealth *state=replica_runtime_health_lookup(rows,count,current.ident);
        if(!state){if(required)return -4;continue;}
        float value=state->health<0?maximum((void*)block):state->health;
        write((void*)(block+0x4c),&value,4);
        if(applied)(*applied)++;
        if(state->growth>=0)write((void*)(block+0x48),&state->growth,4);
        if(state->lifetime>=0)write((void*)(block+0x38),&state->lifetime,4);
    }
    return 0;
}
#endif
