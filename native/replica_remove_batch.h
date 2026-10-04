/* Shared removal policy. Cached roots are lookup candidates, never lifetime
 * authority: copy current membership before reading each candidate again. */
#ifndef REPOPULATED_REPLICA_REMOVE_BATCH_H
#define REPOPULATED_REPLICA_REMOVE_BATCH_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define REPLICA_REMOVE_BATCH_LIMIT 4096
struct ReplicaRemoveDescriptor {
    unsigned ident,command_block;
    int faction;
    unsigned replacement;
};
_Static_assert(sizeof(struct ReplicaRemoveDescriptor)==16,"replica removal descriptor layout");
struct ReplicaRemoveCandidate {unsigned ident;uintptr_t root;};
struct ReplicaRemoveFailure {int row,stage,removed;unsigned ident;uintptr_t root;};
struct ReplicaRemoveAuthority {
    uintptr_t zone,console_zone,field_zone,gate_zone;
    unsigned long thread,field_thread,gate_thread;
    bool configured,mutating;
};
static bool replica_remove_batch_authorized(struct ReplicaRemoveAuthority a){
    return a.configured && a.mutating && a.zone && a.thread && a.zone==a.console_zone &&
        a.zone==a.field_zone && a.zone==a.gate_zone && a.thread==a.field_thread && a.thread==a.gate_thread;
}
struct ReplicaRemoveBatchOps {
    void *context;
    int (*index)(void*,struct ReplicaRemoveCandidate*,int);
    bool (*live_roots)(void*,uintptr_t*,size_t*);
    bool (*identity)(void*,uintptr_t,unsigned);
    int (*remove)(void*,const struct ReplicaRemoveDescriptor*,uintptr_t);
    void (*missing)(void*,unsigned,uintptr_t);
};
static int replica_remove_descriptors_valid(const struct ReplicaRemoveDescriptor *rows,int count,int *bad_row){
    if(bad_row)*bad_row=-1;
    if(count<0 || count>REPLICA_REMOVE_BATCH_LIMIT || (count && !rows))return -1;
    for(int i=0;i<count;i++){
        const struct ReplicaRemoveDescriptor *r=rows+i;
        if(!r->ident || (i && rows[i-1].ident>=r->ident) || r->replacement>2 ||
           (!r->replacement && (r->command_block || r->faction)) ||
           (r->replacement && (!r->command_block || r->faction<0)) ||
           (r->replacement==2 && (r->command_block!=r->ident || r->faction))){
            if(bad_row)*bad_row=i;
            return -2;
        }
    }
    return 0;
}
static uintptr_t replica_remove_candidate(const struct ReplicaRemoveCandidate *rows,int count,unsigned ident){
    int lo=0,hi=count;
    while(lo<hi){int mid=lo+(hi-lo)/2;if(rows[mid].ident<ident)lo=mid+1;else hi=mid;}
    return lo<count && rows[lo].ident==ident?rows[lo].root:0;
}
static int replica_remove_batch_run(const struct ReplicaRemoveDescriptor *rows,int count,
                                    const struct ReplicaRemoveBatchOps *ops,struct ReplicaRemoveFailure *failure){
    struct ReplicaRemoveFailure failed={.row=-1};
    int invalid=replica_remove_descriptors_valid(rows,count,&failed.row);
    if(invalid){failed.stage=invalid;if(failure)*failure=failed;return invalid;}
    if(!count){if(failure)*failure=failed;return 0;}
    if(!ops || !ops->index || !ops->live_roots || !ops->identity || !ops->remove || !ops->missing){
        failed.stage=-1;if(failure)*failure=failed;return -1;
    }
    struct ReplicaRemoveCandidate initial[REPLICA_REMOVE_BATCH_LIMIT];
    int indexed=ops->index(ops->context,initial,REPLICA_REMOVE_BATCH_LIMIT);
    if(indexed<0 || indexed>REPLICA_REMOVE_BATCH_LIMIT){failed.stage=-3;if(failure)*failure=failed;return -3;}
    for(int i=0;i<indexed;i++)if(!initial[i].ident || !initial[i].root || (i && initial[i-1].ident>=initial[i].ident)){
        failed.stage=-3;if(failure)*failure=failed;return -3;
    }
    uintptr_t live[REPLICA_REMOVE_BATCH_LIMIT];
    for(int i=0;i<count;i++){
        unsigned ident=rows[i].ident;
        uintptr_t root=replica_remove_candidate(initial,indexed,ident);
        failed.row=i;failed.ident=ident;failed.root=root;
        if(!root){ops->missing(ops->context,ident,0);continue;}
        size_t length=0;
        if(!ops->live_roots(ops->context,live,&length) || length>REPLICA_REMOVE_BATCH_LIMIT){
            failed.stage=-4;if(failure)*failure=failed;return -4;
        }
        int membership=0;
        for(size_t j=0;j<length;j++)if(live[j]==root)membership++;
        if(!membership){
            /* No reads through root after it disappears. Native recursive
             * removal/deferred release can invalidate a later candidate. */
            ops->missing(ops->context,ident,root);continue;
        }
        if(membership!=1 || !ops->identity(ops->context,root,ident)){
            ops->missing(ops->context,ident,root);
            failed.stage=-5;if(failure)*failure=failed;return -5;
        }
        int removed=ops->remove(ops->context,rows+i,root);
        if(removed<0 || removed>1){failed.stage=-6;if(failure)*failure=failed;return -6;}
        failed.removed+=removed;
    }
    failed.row=-1;failed.stage=0;
    if(failure)*failure=failed;
    return failed.removed;
}
#endif
