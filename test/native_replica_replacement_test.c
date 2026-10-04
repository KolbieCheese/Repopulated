/* Execute the same identity/lifetime decisions and retained curve used by
 * the DLL, against bounded fake native objects. No game process is started. */
#include <assert.h>
#include <stdio.h>
#include "../native/replica_replacement.h"
static unsigned char zones[2][0x198],roots[3][0x180],blocks[3][0xc0],serials[3][0x34];
static int transition;
static bool within(const void *source,size_t size,const void *base,size_t length){
    uintptr_t at=(uintptr_t)source,first=(uintptr_t)base;
    return at>=first && at-first<=length && size<=length-(at-first);
}
static void putptr(void *base,size_t offset,uintptr_t value){memcpy((char*)base+offset,&value,sizeof(value));}
static void putuint(void *base,size_t offset,unsigned value){memcpy((char*)base+offset,&value,sizeof(value));}
static void putint(void *base,size_t offset,int value){memcpy((char*)base+offset,&value,sizeof(value));}
static bool read_fake(const void *source,void *out,size_t size){
    if(!within(source,size,zones,sizeof(zones)) && !within(source,size,roots,sizeof(roots)) &&
       !within(source,size,blocks,sizeof(blocks)) && !within(source,size,serials,sizeof(serials)))return false;
    memcpy(out,source,size);
    if(transition && source==serials[0]+0x10){
        if(transition==1)putuint(serials[0],8,0x70000001u);
        if(transition==2)putint(serials[0],0x10,100);
        if(transition==3)putuint(blocks[0],0x30,0x322u);
        if(transition==4)putptr(blocks[0],0xb8,(uintptr_t)roots[2]);
        if(transition==5)putptr(roots[0],8,(uintptr_t)zones[1]);
        if(transition==6)putptr(roots[0],0x178,(uintptr_t)roots[2]);
        if(transition==7)putptr(roots[0],0x108,(uintptr_t)blocks[2]);
        transition=0;
    }
    return true;
}
static struct ReplicaSmoothing fixture(void){
    memset(zones,0,sizeof(zones));memset(roots,0,sizeof(roots));memset(blocks,0,sizeof(blocks));memset(serials,0,sizeof(serials));transition=0;
    for(int i=0;i<3;i++){
        putptr(roots[i],8,(uintptr_t)zones[0]);putptr(roots[i],0x108,(uintptr_t)blocks[i]);
        putint(roots[i],0x118,100); /* Deliberately stale native faction cache. */
        putptr(blocks[i],0xb8,(uintptr_t)roots[i]);putptr(blocks[i],0x28,(uintptr_t)serials[i]);
        putuint(blocks[i],0x30,0x321u);uint64_t features=1;memcpy(blocks[i]+0x40,&features,sizeof(features));
        putuint(serials[i],8,0x70000002u);putint(serials[i],0x10,20008);
    }
    struct ReplicaSmoothing row={.ident=0x70000002u,.motion_sequence=77,.motion_cluster=(uintptr_t)roots[0],
        .last_x=361.25f,.last_y=-88.5f,.last_angle=2.75f,.presented=true,.received=1240.125,.motion_received=1240.125};
    for(int i=0;i<4;i++){
        double at=1000+i*70;struct ReplicaTimelinePose p={300+i*14,-100+i*7,200,100,2.7+i*0.07,1};
        replica_timeline_accept(&row.timeline,p,at,at+30);
        replica_interpolation_accept(&row.interpolation,p,at);
    }
    return row;
}
static void retained(const struct ReplicaSmoothing *row,const struct ReplicaSmoothing *before){
    assert(row->ident==before->ident && row->motion_sequence==before->motion_sequence);
    assert(row->received==before->received && row->motion_received==before->motion_received);
    assert(!memcmp(&row->timeline,&before->timeline,sizeof(row->timeline)));
    assert(!memcmp(&row->interpolation,&before->interpolation,sizeof(row->interpolation)));
    assert(row->last_x==before->last_x && row->last_y==before->last_y && row->last_angle==before->last_angle && row->presented==before->presented);
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
static void change_object(int object,int kind){
    if(kind==0)putuint(blocks[object],0x30,0x322u); /* Real respawn command identity. */
    if(kind==1)putint(serials[object],0x10,100);
    if(kind==2)putuint(serials[object],8,0x70000001u);
    if(kind==3)putptr(blocks[object],0xb8,(uintptr_t)roots[2]);
    if(kind==4)putptr(roots[object],8,(uintptr_t)zones[1]);
    if(kind==5)putptr(roots[object],0x178,(uintptr_t)roots[2]);
    if(kind==6)putptr(roots[object],0x108,0);
    if(kind==7)putptr(blocks[object],0x28,0);
    if(kind==8)putuint(blocks[object],0x30,0);
    if(kind==9){uint64_t features=0;memcpy(blocks[object]+0x40,&features,sizeof(features));}
    if(kind==10)putptr(roots[object],0x108,0x12345678u); /* Unreadable command. */
    if(kind==11)putint(serials[object],0x10,-1);
}
int main(void){
    struct ReplicaSmoothing row=fixture(),before=row;
    assert(replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));
    retained(&row,&before);assert(!row.motion_cluster && row.replacement_pending && row.replacement_initialize);
    assert(row.replacement.zone==(uintptr_t)zones[0] && row.replacement.ident==0x70000002u && row.replacement.command_block==0x321u);
    /* The old native root/command/serial addresses are absent from the ticket. */
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_REPLACEMENT);
    retained(&row,&before);assert(row.motion_cluster==(uintptr_t)roots[1] && !row.replacement_pending && row.replacement_initialize);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_UNCHANGED);
    struct ReplicaMotionDecision replay=replica_replacement_motion_decision(&row,77);
    assert(replay.duplicate && !replay.accept && replay.initialize);
    retained(&row,&before); /* Duplicate replay cannot append or reconcile this curve. */
    replica_replacement_initialized(&row);replay=replica_replacement_motion_decision(&row,77);
    assert(replay.duplicate && !replay.accept && !replay.initialize);
    replay=replica_replacement_motion_decision(&row,78);assert(!replay.duplicate && replay.accept && replay.initialize);
    replay=replica_replacement_motion_decision(&row,0);assert(!replay.duplicate && replay.accept && replay.initialize);
    /* A same-address allocation still revalidates the command generation. */
    row=fixture();before=row;
    assert(replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[0],read_fake)==REPLICA_ROOT_REPLACEMENT);retained(&row,&before);
    change_object(0,0);
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[0],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
    for(int kind=0;kind<12;kind++){
        row=fixture();change_object(0,kind);
        assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));cleared(&row);assert(!row.motion_cluster);
        row=fixture();assert(replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));
        change_object(1,kind);assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
        replay=replica_replacement_motion_decision(&row,77);assert(!replay.duplicate && replay.accept && replay.initialize);
    }
    for(int mode=1;mode<=7;mode++){
        row=fixture();transition=mode;
        assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));cleared(&row);
    }
    /* Only an explicit validated removal authorizes a different native root. */
    row=fixture();assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
    row=fixture();assert(!replica_replacement_detach(&row,zones[0],0,0x321u,20008,read_fake));cleared(&row);
    row=fixture();assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0,20008,read_fake));cleared(&row);
    row=fixture();assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,-1,read_fake));cleared(&row);
    row=fixture();row.motion_sequence=0;assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));cleared(&row);
    row=fixture();row.timeline.valid=false;assert(!replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));cleared(&row);
    row=fixture();assert(replica_replacement_detach(&row,zones[0],(uintptr_t)roots[0],0x321u,20008,read_fake));
    assert(replica_replacement_bind(&row,zones[1],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_RESET);cleared(&row);
    row=(struct ReplicaSmoothing){.ident=0x70000002u};
    assert(replica_replacement_bind(&row,zones[0],(uintptr_t)roots[1],read_fake)==REPLICA_ROOT_FIRST);
    puts("replica replacement: retained source curve, duplicate body replay, strict generation/ownership and lifetime rejection passed");
    return 0;
}
