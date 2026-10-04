/* Exercise the production batch policy against vector mutation and reused
 * readable addresses. No native pointer is touched by this test. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../native/replica_remove_batch.h"
struct Root {uintptr_t address;unsigned ident,command;int faction;bool live,readable,top,owned;};
struct Fixture {
    struct Root roots[8];
    bool history[8],publication[8],trace[8],preserved[8];
    int indexed,copied,identity_reads,removed,missing,invalidated,killed,freed;
    int mode,fail_copy_at,bad_index,duplicate_live;
    unsigned last_missing,last_identity,last_removed;
};
static int compare(const void *a,const void *b){
    unsigned x=((const struct ReplicaRemoveCandidate*)a)->ident,y=((const struct ReplicaRemoveCandidate*)b)->ident;
    return (x>y)-(x<y);
}
static struct Fixture fixture(void){
    struct Fixture f={0};
    for(int i=0;i<3;i++){
        f.roots[i]=(struct Root){(uintptr_t)(0x1000+i*0x200),(unsigned)(i+1),(unsigned)(100+i),8,true,true,true,true};
        f.history[i]=f.publication[i]=f.trace[i]=true;
    }
    return f;
}
static struct Root *find(struct Fixture *f,uintptr_t pointer){
    for(int i=0;i<8;i++)if(f->roots[i].address==pointer)return f->roots+i;
    return NULL;
}
static int index_roots(void *context,struct ReplicaRemoveCandidate *out,int capacity){
    struct Fixture *f=context;f->indexed++;assert(f->indexed==1);int count=0;
    for(int i=0;i<8;i++)if(f->roots[i].live && f->roots[i].top){
        assert(count<capacity);out[count++]=(struct ReplicaRemoveCandidate){f->roots[i].ident,f->roots[i].address};
    }
    qsort(out,count,sizeof(*out),compare);
    if(f->bad_index==1)return -1;
    if(f->bad_index==2)return REPLICA_REMOVE_BATCH_LIMIT+1;
    if(f->bad_index==3 && count>1)out[1].ident=out[0].ident;
    if(f->bad_index==4 && count)out[0].root=0;
    return count;
}
static bool copy_live(void *context,uintptr_t *out,size_t *count){
    struct Fixture *f=context;f->copied++;
    if(f->fail_copy_at==f->copied)return false;
    *count=0;
    /* Deliberately reverse current membership: cached slot numbers cannot
     * survive native swap-erasure even when addresses remain stable. */
    for(int i=7;i>=0;i--)if(f->roots[i].live)out[(*count)++]=f->roots[i].address;
    if(f->duplicate_live && *count)out[(*count)++]=out[0];
    return true;
}
static bool identity(void *context,uintptr_t pointer,unsigned expected){
    struct Fixture *f=context;struct Root *r=find(f,pointer);
    /* A violation fails here even if a freed pool slot is still readable. */
    assert(f->copied>0 && r && r->live && r->readable);
    f->identity_reads++;f->last_identity=expected;
    return r->ident==expected && r->top && r->owned;
}
static void missing(void *context,unsigned expected,uintptr_t pointer){
    struct Fixture *f=context;f->missing++;f->last_missing=expected;
    /* Only stable ID and address comparisons are allowed; do not inspect the
     * old root, which may be inaccessible or already contain another actor. */
    (void)pointer;
    if(expected>=1 && expected<=8){int i=(int)expected-1;f->history[i]=f->publication[i]=f->trace[i]=false;}
}
static void kill_root(struct Fixture *f,struct Root *r){
    int i=(int)(r-f->roots);
    assert(!f->publication[i] && !f->trace[i]); /* invalidate before native death/free */
    f->killed++;r->live=false;f->freed++;r->readable=false;
}
static int remove_one(void *context,const struct ReplicaRemoveDescriptor *row,uintptr_t pointer){
    struct Fixture *f=context;struct Root *r=find(f,pointer);assert(r && r->live && r->ident==row->ident);
    assert(f->last_identity==row->ident);int i=(int)(r-f->roots);
    f->invalidated++;f->publication[i]=f->trace[i]=false;
    f->preserved[i]=(row->replacement==1 && row->command_block==r->command && row->faction==r->faction) ||
        (row->replacement==2 && !r->command && row->command_block==r->ident && !row->faction && !r->faction);
    if(!f->preserved[i])f->history[i]=false;
    f->removed++;f->last_removed=row->ident;kill_root(f,r);
    if(f->removed==1){
        if(f->mode==1){ /* recursive/native cleanup also removes a later candidate */
            f->roots[1].live=false;f->roots[1].readable=false;
        }
        if(f->mode==2){ /* pool reuse: same address, different live identity */
            f->roots[1].ident=99;f->roots[1].command=999;
        }
        if(f->mode==3)f->roots[1].top=false;
        if(f->mode==4)f->roots[1].owned=false;
        if(f->mode==5){ /* fresh unrelated root was absent from the initial index */
            f->roots[3]=(struct Root){0x2000,4,104,8,true,true,true,true};
        }
    }
    return 1;
}
static int run(struct Fixture *f,const struct ReplicaRemoveDescriptor *rows,int count,struct ReplicaRemoveFailure *error){
    struct ReplicaRemoveBatchOps ops={f,index_roots,copy_live,identity,remove_one,missing};
    return replica_remove_batch_run(rows,count,&ops,error);
}
static void untouched(const struct Fixture *f){assert(!f->indexed && !f->copied && !f->identity_reads && !f->removed && !f->missing);}
int main(void){
    struct ReplicaRemoveAuthority authority={1,1,1,1,7,7,7,true,true};
    assert(replica_remove_batch_authorized(authority));
    for(int change=0;change<10;change++){
        struct ReplicaRemoveAuthority bad=authority;
        if(change==0)bad.zone=0;if(change==1)bad.console_zone=2;if(change==2)bad.field_zone=2;
        if(change==3)bad.gate_zone=2;if(change==4)bad.thread=0;if(change==5)bad.field_thread=8;
        if(change==6)bad.gate_thread=8;if(change==7)bad.configured=false;if(change==8)bad.mutating=false;
        if(change==9)bad.console_zone=0;
        assert(!replica_remove_batch_authorized(bad));
    }
    struct ReplicaRemoveDescriptor rows[3]={{1,0,0,0},{2,0,0,0},{3,0,0,0}};
    struct ReplicaRemoveFailure error;struct Fixture f=fixture();
    assert(run(&f,rows,3,&error)==3 && f.indexed==1 && f.copied==3 && f.identity_reads==3);
    assert(f.removed==3 && f.invalidated==3 && f.killed==3 && f.freed==3 && !error.stage);
    f=fixture();f.mode=1;
    assert(run(&f,rows,3,&error)==2 && f.indexed==1 && f.copied==3 && f.identity_reads==2 && f.missing==1);
    assert(!f.history[1] && !f.publication[1] && !f.trace[1]);
    f=fixture();f.roots[1].live=false;f.roots[1].readable=false;
    assert(run(&f,rows,3,&error)==2 && f.copied==2 && f.missing==1);
    for(int mode=2;mode<=4;mode++){
        f=fixture();f.mode=mode;
        assert(run(&f,rows,3,&error)==-5 && error.row==1 && error.ident==2 && error.removed==1);
        assert(f.removed==1 && f.identity_reads==2 && f.roots[1].live && f.roots[2].live);
        assert(!f.history[1] && !f.publication[1] && !f.trace[1]);
    }
    f=fixture();f.mode=5;struct ReplicaRemoveDescriptor new_root[2]={{1,0,0,0},{4,0,0,0}};
    assert(run(&f,new_root,2,&error)==1 && f.roots[3].live && f.last_missing==4 && f.identity_reads==1);
    f=fixture();f.duplicate_live=1;
    assert(run(&f,rows+2,1,&error)==-5 && !f.identity_reads && !f.removed && f.missing==1);
    for(int failure=1;failure<=4;failure++){
        f=fixture();f.bad_index=failure;
        assert(run(&f,rows,3,&error)==-3 && !f.removed && !f.copied && !f.identity_reads);
    }
    f=fixture();f.fail_copy_at=2;
    assert(run(&f,rows,3,&error)==-4 && error.removed==1 && error.row==1 && f.removed==1 && f.identity_reads==1);
    f=fixture();struct ReplicaRemoveDescriptor replacement={1,100,8,1};
    assert(run(&f,&replacement,1,&error)==1 && f.preserved[0] && f.history[0] && !f.publication[0] && !f.trace[0]);
    f=fixture();f.roots[0].command=101;
    assert(run(&f,&replacement,1,&error)==1 && !f.preserved[0] && !f.history[0]);
    f=fixture();f.roots[0].command=0;f.roots[0].faction=0;
    struct ReplicaRemoveDescriptor fragment={1,1,0,2};
    assert(run(&f,&fragment,1,&error)==1 && f.preserved[0] && f.history[0] && !f.publication[0] && !f.trace[0]);
    f=fixture();
    assert(run(&f,&fragment,1,&error)==1 && !f.preserved[0] && !f.history[0]);
    for(int change=0;change<9;change++){
        struct ReplicaRemoveDescriptor bad[3];memcpy(bad,rows,sizeof(bad));
        if(change==0)bad[2].ident=0;if(change==1)bad[2].ident=2;if(change==2)bad[1].ident=4;
        if(change==3)bad[2].replacement=3;if(change==4)bad[2].command_block=1;
        if(change==5)bad[2].faction=8;if(change==6)bad[2].replacement=1;
        if(change==7){bad[2].replacement=1;bad[2].command_block=8;bad[2].faction=-1;}
        if(change==8){bad[2].faction=-1;}
        f=fixture();assert(run(&f,bad,3,&error)==-2);untouched(&f);
    }
    for(int change=0;change<3;change++){
        struct ReplicaRemoveDescriptor bad=fragment;
        if(change==0)bad.command_block=2;
        if(change==1)bad.faction=8;
        if(change==2)bad.command_block=0;
        f=fixture();assert(run(&f,&bad,1,&error)==-2);untouched(&f);
    }
    f=fixture();assert(run(&f,NULL,0,&error)==0);untouched(&f);
    f=fixture();assert(run(&f,NULL,1,&error)==-1);untouched(&f);
    f=fixture();assert(run(&f,rows,-1,&error)==-1);untouched(&f);
    f=fixture();assert(run(&f,rows,REPLICA_REMOVE_BATCH_LIMIT+1,&error)==-1);untouched(&f);
    struct ReplicaRemoveDescriptor maximum[REPLICA_REMOVE_BATCH_LIMIT];
    for(unsigned i=0;i<REPLICA_REMOVE_BATCH_LIMIT;i++)maximum[i]=(struct ReplicaRemoveDescriptor){i+1,0,0,0};
    f=fixture();assert(run(&f,maximum,REPLICA_REMOVE_BATCH_LIMIT,&error)==3 && f.indexed==1 && f.missing==4093);
    puts("batch preflight, authority, recursive removal, fresh membership and pointer-reuse rejection passed");
    return 0;
}
