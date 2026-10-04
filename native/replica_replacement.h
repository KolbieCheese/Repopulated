/* Shape changes may replace a native body while the authoritative command
 * or commandless minimum-block anchor survives. A ticket keeps only identity;
 * it never keeps the removed native root/command/serial pointers alive. */
#ifndef REPOPULATED_REPLICA_REPLACEMENT_H
#define REPOPULATED_REPLICA_REPLACEMENT_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include "replica_timeline.h"
typedef bool (*ReplicaReplacementRead)(const void*,void*,size_t);
struct ReplicaReplacementIdentity {
    uintptr_t zone;
    unsigned ident,command_block;
    int faction;
    unsigned kind;
};
enum ReplicaReplacementKind {REPLICA_REPLACEMENT_COMMAND=1,REPLICA_REPLACEMENT_FRAGMENT=2};
struct ReplicaSmoothing {
    unsigned ident,motion_sequence;float last_x,last_y,last_angle,dx,dy,da;
    bool presented;double received,motion_received;
    uintptr_t motion_cluster;
    struct ReplicaTimeline timeline;
    struct ReplicaInterpolationBuffer interpolation;
    struct ReplicaReplacementIdentity replacement;
    bool replacement_pending,replacement_initialize;
};
/* The canonical faction lives in the owned command's serial data. Native
 * faction caches can lag, and cached commands on debris can belong elsewhere. */
static bool replica_replacement_identity_once(void *zone,uintptr_t root,
                                             struct ReplicaReplacementIdentity *out,ReplicaReplacementRead read){
    uintptr_t owner,parent,command,serial,block_owner;uint64_t features;
    unsigned block_id,ident;int faction;
    if(!zone || !root || !out || !read ||
       !read((void*)(root+8),&owner,sizeof(owner)) || owner!=(uintptr_t)zone ||
       !read((void*)(root+0x178),&parent,sizeof(parent)) || parent ||
       !read((void*)(root+0x108),&command,sizeof(command)) || !command ||
       !read((void*)(command+0xb8),&block_owner,sizeof(block_owner)) || block_owner!=root ||
       !read((void*)(command+0x40),&features,sizeof(features)) || !(features&1) ||
       !read((void*)(command+0x30),&block_id,sizeof(block_id)) || !block_id ||
       !read((void*)(command+0x28),&serial,sizeof(serial)) || !serial ||
       !read((void*)(serial+8),&ident,sizeof(ident)) || !ident ||
       !read((void*)(serial+0x10),&faction,sizeof(faction)) || faction<0)return false;
    *out=(struct ReplicaReplacementIdentity){(uintptr_t)zone,ident,block_id,faction,REPLICA_REPLACEMENT_COMMAND};
    return true;
}
static int replica_replacement_compare_block_ids(const void *a,const void *b){
    unsigned x=*(const unsigned*)a,y=*(const unsigned*)b;return (x>y)-(x<y);
}
static bool replica_replacement_fragment_once(void *zone,uintptr_t root,
                                             struct ReplicaReplacementIdentity *out,ReplicaReplacementRead read){
    unsigned char root_header[0x180];
    uintptr_t owner,parent,command,block_owner,vector[2],blocks[4096];unsigned identities[4096];
    if(!zone || !root || !out || !read || !read((void*)root,root_header,sizeof(root_header)))return false;
    memcpy(&owner,root_header+8,sizeof(owner));memcpy(&parent,root_header+0x178,sizeof(parent));
    memcpy(&command,root_header+0x108,sizeof(command));memcpy(vector,root_header+0xf0,sizeof(vector));
    if(owner!=(uintptr_t)zone || parent)return false;
    /* A cached command on detached debris can belong to another root. Never
     * promote that foreign pointer into this root's command generation. */
    if(command){
        uint64_t features;
        if(!read((void*)(command+0xb8),&block_owner,sizeof(block_owner)))return false;
        if(block_owner==root &&
           (!read((void*)(command+0x40),&features,sizeof(features)) || (features&1)))return false;
    }
    if(vector[1]<vector[0] ||
       (vector[1]-vector[0])%sizeof(uintptr_t) || vector[1]-vector[0]>sizeof(blocks) ||
       vector[1]==vector[0] || !vector[0])return false;
    size_t count=(vector[1]-vector[0])/sizeof(uintptr_t);
    if(!read((void*)vector[0],blocks,count*sizeof(*blocks)))return false;
    for(size_t i=0;i<count;i++){
        unsigned char header[0x90];unsigned ident;uint64_t features;
        if(!blocks[i] || !read((void*)(blocks[i]+0x30),header,sizeof(header)))return false;
        memcpy(&ident,header,sizeof(ident));memcpy(&features,header+0x10,sizeof(features));
        memcpy(&block_owner,header+0x88,sizeof(block_owner));
        if(!ident || block_owner!=root || (features&1))return false;
        identities[i]=ident;
    }
    qsort(identities,count,sizeof(*identities),replica_replacement_compare_block_ids);
    for(size_t i=1;i<count;i++)if(identities[i]==identities[i-1])return false;
    unsigned minimum=identities[0];
    *out=(struct ReplicaReplacementIdentity){(uintptr_t)zone,minimum,minimum,0,REPLICA_REPLACEMENT_FRAGMENT};
    return true;
}
static bool replica_replacement_identity_equal(struct ReplicaReplacementIdentity a,struct ReplicaReplacementIdentity b){
    return a.zone==b.zone && a.ident==b.ident && a.command_block==b.command_block && a.faction==b.faction && a.kind==b.kind;
}
static bool replica_replacement_matches(void *zone,uintptr_t root,
                                       struct ReplicaReplacementIdentity expected,ReplicaReplacementRead read){
    struct ReplicaReplacementIdentity first,second;
    /* Recheck ownership/identity across reads, rather than accepting a mixed
     * snapshot if a pointer or serial field changes during validation. */
    if(expected.zone!=(uintptr_t)zone || !expected.ident || !expected.command_block || expected.faction<0)return false;
    if(expected.kind==REPLICA_REPLACEMENT_COMMAND)
        return replica_replacement_identity_once(zone,root,&first,read) && replica_replacement_identity_equal(first,expected) &&
            replica_replacement_identity_once(zone,root,&second,read) && replica_replacement_identity_equal(second,expected);
    if(expected.kind!=REPLICA_REPLACEMENT_FRAGMENT || expected.command_block!=expected.ident || expected.faction)return false;
    return replica_replacement_fragment_once(zone,root,&first,read) && replica_replacement_identity_equal(first,expected) &&
        replica_replacement_fragment_once(zone,root,&second,read) && replica_replacement_identity_equal(second,expected);
}
static bool replica_replacement_detach_kind(struct ReplicaSmoothing *row,void *zone,uintptr_t root,
                                           unsigned command_block,int faction,unsigned kind,ReplicaReplacementRead read){
    if(!row)return false;
    struct ReplicaReplacementIdentity expected={(uintptr_t)zone,row->ident,command_block,faction,kind};
    if(!root || row->motion_cluster!=root || !row->motion_sequence || !row->timeline.valid ||
       !replica_replacement_matches(zone,root,expected,read)){
        memset(row,0,sizeof(*row));return false;
    }
    row->motion_cluster=0;row->replacement=expected;
    row->replacement_pending=true;row->replacement_initialize=true;
    return true;
}
static inline bool replica_replacement_detach(struct ReplicaSmoothing *row,void *zone,uintptr_t root,
                                              unsigned command_block,int faction,ReplicaReplacementRead read){
    return replica_replacement_detach_kind(row,zone,root,command_block,faction,REPLICA_REPLACEMENT_COMMAND,read);
}
enum ReplicaRootBinding {REPLICA_ROOT_UNCHANGED,REPLICA_ROOT_FIRST,REPLICA_ROOT_REPLACEMENT,REPLICA_ROOT_RESET};
static enum ReplicaRootBinding replica_replacement_bind(struct ReplicaSmoothing *row,void *zone,uintptr_t root,
                                                       ReplicaReplacementRead read){
    unsigned ident=row->ident;
    if(row->replacement_pending){
        if(!row->motion_cluster && replica_replacement_matches(zone,root,row->replacement,read)){
            row->motion_cluster=root;row->replacement_pending=false;
            return REPLICA_ROOT_REPLACEMENT;
        }
        memset(row,0,sizeof(*row));row->ident=ident;row->motion_cluster=root;
        return REPLICA_ROOT_RESET;
    }
    if(row->replacement_initialize &&
       !replica_replacement_matches(zone,root,row->replacement,read)){
        /* The presenter may bind first. Revalidate before duplicate replay
         * initializes the body, including a pool reuse at the same address. */
        memset(row,0,sizeof(*row));row->ident=ident;row->motion_cluster=root;
        return REPLICA_ROOT_RESET;
    }
    if(row->motion_cluster==root)return REPLICA_ROOT_UNCHANGED;
    /* An unannounced pointer change is a new native lifetime. Matching actor
     * IDs alone do not permit interpolation across respawn or lost ownership. */
    if(row->motion_cluster || row->motion_sequence){
        memset(row,0,sizeof(*row));row->ident=ident;row->motion_cluster=root;
        return REPLICA_ROOT_RESET;
    }
    row->motion_cluster=root;return REPLICA_ROOT_FIRST;
}
struct ReplicaMotionDecision {bool duplicate,accept,initialize;};
static struct ReplicaMotionDecision replica_replacement_motion_decision(const struct ReplicaSmoothing *row,unsigned sequence){
    bool duplicate=sequence && row->motion_sequence==sequence;
    return (struct ReplicaMotionDecision){duplicate,!duplicate,!duplicate || row->replacement_initialize};
}
static void replica_replacement_initialized(struct ReplicaSmoothing *row){row->replacement_initialize=false;}
#endif
