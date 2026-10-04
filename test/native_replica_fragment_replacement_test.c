/* Execute production neutral replacement validation against bounded native
 * layouts, including pool reuse and mutation between the two fresh reads. */
#include <assert.h>
#include <stdio.h>
#include "../native/replica_replacement.h"
static unsigned char zones[2][0x198],roots[3][0x180],blocks[3][4096][0xc0];
static uintptr_t vectors[3][4097];
static size_t reads,block_count;
static int transition=-1;
static bool within(const void *source,size_t size,const void *base,size_t length){
    uintptr_t at=(uintptr_t)source,first=(uintptr_t)base;
    return at>=first && at-first<=length && size<=length-(at-first);
}
static void putptr(void *base,size_t offset,uintptr_t value){memcpy((char*)base+offset,&value,sizeof(value));}
static void putuint(void *base,size_t offset,unsigned value){memcpy((char*)base+offset,&value,sizeof(value));}
static void putfeatures(void *base,uint64_t value){memcpy((char*)base+0x40,&value,sizeof(value));}
static void change(int bank,int kind){
    if(kind==0)putuint(blocks[bank][0],0x30,9999); /* Lost minimum anchor. */
    if(kind==1)putuint(blocks[bank][1],0x30,400); /* Different root minimum. */
    if(kind==2)putptr(blocks[bank][1],0xb8,(uintptr_t)roots[2]);
    if(kind==3)putptr(roots[bank],8,(uintptr_t)zones[1]);
    if(kind==4)putptr(roots[bank],0x178,(uintptr_t)roots[2]);
    if(kind==5){putptr(roots[bank],0x108,(uintptr_t)blocks[bank][0]);putfeatures(blocks[bank][0],1);}
    if(kind==6)putfeatures(blocks[bank][1],1);
    if(kind==7)putuint(blocks[bank][2],0x30,502); /* Non-anchor duplicate. */
    if(kind==8)putuint(blocks[bank][1],0x30,0);
    if(kind==9)putptr(roots[bank],0xf8,(uintptr_t)vectors[bank]+block_count*8+1);
    if(kind==10)putptr(roots[bank],0xf8,(uintptr_t)vectors[bank]);
    if(kind==11){putptr(roots[bank],0xf0,0x12345678);putptr(roots[bank],0xf8,0x12345690);}
    if(kind==12)vectors[bank][1]=0x12345678;
    if(kind==13)putptr(roots[bank],0x108,0x12345678);
    if(kind==14)putptr(roots[bank],0xf8,(uintptr_t)vectors[bank]+4097*8);
}
static bool read_fake(const void *source,void *out,size_t size){
    reads++;
    if(!within(source,size,zones,sizeof(zones)) && !within(source,size,roots,sizeof(roots)) &&
       !within(source,size,blocks,sizeof(blocks)) && !within(source,size,vectors,sizeof(vectors)))return false;
    memcpy(out,source,size);
    if(transition>=0 && source==blocks[0][block_count-1]+0x30){
        int kind=transition;transition=-1;change(0,kind);
    }
    return true;
}
static struct ReplicaSmoothing fixture(size_t count){
    memset(zones,0,sizeof(zones));memset(roots,0,sizeof(roots));memset(blocks,0,sizeof(blocks));memset(vectors,0,sizeof(vectors));
    reads=0;transition=-1;block_count=count;
    for(int bank=0;bank<3;bank++){
        putptr(roots[bank],8,(uintptr_t)zones[0]);
        putptr(roots[bank],0xf0,(uintptr_t)vectors[bank]);putptr(roots[bank],0xf8,(uintptr_t)vectors[bank]+count*8);
        for(size_t i=0;i<count;i++){
            vectors[bank][i]=(uintptr_t)blocks[bank][i];
            putuint(blocks[bank][i],0x30,501+(unsigned)i);putptr(blocks[bank][i],0xb8,(uintptr_t)roots[bank]);
            putfeatures(blocks[bank][i],0x400);
        }
    }
    struct ReplicaSmoothing row={.ident=501,.motion_sequence=77,.motion_cluster=(uintptr_t)roots[0],
        .last_x=361.25f,.last_y=-88.5f,.last_angle=3.12f,.presented=true,.received=1240.125,.motion_received=1240.125};
    for(int i=0;i<4;i++){
        double at=1000+i*70;struct ReplicaTimelinePose p={300+i*14,-100+i*7,200,100,3.0+i*.07,1};
        replica_timeline_accept(&row.timeline,p,at,at+30);replica_interpolation_accept(&row.interpolation,p,at);
    }
    return row;
}
static bool detach(struct ReplicaSmoothing *row){
    return replica_replacement_detach_kind(row,zones[0],(uintptr_t)roots[0],501,0,REPLICA_REPLACEMENT_FRAGMENT,read_fake);
}
static void retained(const struct ReplicaSmoothing *row,const struct ReplicaSmoothing *before){
    assert(row->ident==before->ident && row->motion_sequence==before->motion_sequence);
    assert(row->received==before->received && row->motion_received==before->motion_received);
    assert(!memcmp(&row->timeline,&before->timeline,sizeof(row->timeline)));
    assert(!memcmp(&row->interpolation,&before->interpolation,sizeof(row->interpolation)));
    assert(row->last_x==before->last_x && row->last_y==before->last_y && row->last_angle==before->last_angle);
    for(int ms=1080;ms<=1300;ms++){
        struct ReplicaTimelinePose a=replica_interpolation_evaluate(&row->interpolation,ms);
        struct ReplicaTimelinePose b=replica_interpolation_evaluate(&before->interpolation,ms);
        assert(a.x==b.x && a.y==b.y && a.angle==b.angle && a.vx==b.vx && a.vy==b.vy && a.angular==b.angular);
    }
}
static void cleared(const struct ReplicaSmoothing *row){
    assert(!row->motion_sequence && !row->timeline.valid && !row->interpolation.count && !row->presented);
    assert(!row->replacement_pending && !row->replacement_initialize && !row->replacement.ident);
}
int main(void){
    struct ReplicaSmoothing row=fixture(3),before=row;
    assert(detach(&row));retained(&row,&before);
    assert(row.replacement.kind==REPLICA_REPLACEMENT_FRAGMENT && row.replacement.command_block==row.ident && !row.replacement.faction);
    /* Geometry may reorder/add/remove non-anchor blocks without losing this
     * lifetime. No old object address is retained in the ticket. */
    uintptr_t swap=vectors[1][0];vectors[1][0]=vectors[1][2];vectors[1][2]=swap;
    putuint(blocks[1][1],0x30,600);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_REPLACEMENT);retained(&row,&before);
    struct ReplicaMotionDecision replay=replica_replacement_motion_decision(&row,77);
    assert(replay.duplicate && !replay.accept && replay.initialize);
    replica_replacement_initialized(&row);replay=replica_replacement_motion_decision(&row,77);
    assert(replay.duplicate && !replay.accept && !replay.initialize);
    /* A foreign cached COMMAND never establishes local ownership. */
    row=fixture(3);before=row;putptr(roots[0],0x108,(uintptr_t)blocks[2][0]);putfeatures(blocks[2][0],1);
    assert(detach(&row));retained(&row,&before);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_REPLACEMENT);retained(&row,&before);
    for(int kind=0;kind<15;kind++){
        row=fixture(3);change(0,kind);assert(!detach(&row));cleared(&row);
        row=fixture(3);assert(detach(&row));change(1,kind);
        assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
        row=fixture(3);transition=kind;assert(!detach(&row));cleared(&row);
    }
    row=fixture(3);assert(!replica_replacement_detach_kind(&row,zones[0],(uintptr_t)roots[0],502,0,2,read_fake));cleared(&row);
    row=fixture(3);assert(!replica_replacement_detach_kind(&row,zones[0],(uintptr_t)roots[0],501,8,2,read_fake));cleared(&row);
    row=fixture(3);assert(!replica_replacement_detach_kind(&row,zones[0],(uintptr_t)roots[0],501,0,3,read_fake));cleared(&row);
    row=fixture(3);assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],501,0,read_fake));cleared(&row);
    row=fixture(3);assert(detach(&row));
    assert(replica_replacement_bind(&row,zones[1],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
    /* A readable same-address pool reuse with a different anchor resets. */
    row=fixture(3);assert(detach(&row));change(0,0);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[0],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
    row=fixture(4096);before=row;assert(detach(&row));assert(reads==2*(4096+2));retained(&row,&before);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_REPLACEMENT);
    assert(reads==4*(4096+2));retained(&row,&before);
    puts("neutral replacement: stable minimum anchor, unchanged curve, strict owners/generation and 4096 bulk-header budget passed");
    return 0;
}
