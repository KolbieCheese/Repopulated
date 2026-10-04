/* Cosmetic controls on the displayed source timeline. No native object
 * pointers, simulation integration or synchronization live in this helper. */
#ifndef REPOPULATED_MOVER_WINDOW_H
#define REPOPULATED_MOVER_WINDOW_H
#include <stdbool.h>
#include <math.h>
#include <string.h>
#define REPLICA_MOVER_CAPACITY 4096
struct ReplicaMover {unsigned ident,block_ident;float accel,angular;};
_Static_assert(sizeof(struct ReplicaMover)==16,"mover state layout");
struct ReplicaMoverWindow {
    struct ReplicaMover a[REPLICA_MOVER_CAPACITY],b[REPLICA_MOVER_CAPACITY];
    int count_a,count_b;double source_a,source_b;bool valid,future;
};
static int replica_mover_rows_valid(const struct ReplicaMover *rows,int count){
    if(count<0 || count>REPLICA_MOVER_CAPACITY || (count && !rows))return -1;
    for(int i=0;i<count;i++)if(!rows[i].ident || !rows[i].block_ident ||
       (i && rows[i-1].block_ident>=rows[i].block_ident) ||
       !isfinite(rows[i].accel) || rows[i].accel<0 || rows[i].accel>1.001f ||
       !isfinite(rows[i].angular) || fabsf(rows[i].angular)>1.001f)return -2;
    return count;
}
static int replica_mover_window_accept(struct ReplicaMoverWindow *w,
                                      const struct ReplicaMover *a,int count_a,double source_a,
                                      const struct ReplicaMover *b,int count_b,double source_b){
    if(!w)return -1;
    int result=replica_mover_rows_valid(a,count_a);if(result<0)return result;
    result=replica_mover_rows_valid(b,count_b);if(result<0)return result;
    if(!isfinite(source_a) || !isfinite(source_b) || source_a<=0 || source_b<source_a || source_b-source_a>500)return -3;
    /* Validate everything before mutating the active window. An empty B with
     * time A means no future frame; empty B at a later time is authoritative. */
    if(count_a)memcpy(w->a,a,(size_t)count_a*sizeof(*a));
    if(count_b)memcpy(w->b,b,(size_t)count_b*sizeof(*b));
    w->count_a=count_a;w->count_b=count_b;w->source_a=source_a;w->source_b=source_b;
    w->valid=true;w->future=count_b>0 || source_b>source_a;return count_a;
}
static const struct ReplicaMover *replica_mover_lookup(const struct ReplicaMover *rows,int count,unsigned ident,unsigned bid){
    int lo=0,hi=count;while(lo<hi){int mid=lo+(hi-lo)/2;if(rows[mid].block_ident<bid)lo=mid+1;else hi=mid;}
    return lo<count && rows[lo].block_ident==bid && rows[lo].ident==ident?rows+lo:NULL;
}
static float replica_mover_accel(float value){return fmaxf(0,fminf(1,value));}
static float replica_mover_angular(float value){return fmaxf(-1,fminf(1,value));}
static bool replica_mover_window_evaluate(const struct ReplicaMoverWindow *w,unsigned ident,unsigned bid,double target,float out[2]){
    if(!out)return false;out[0]=out[1]=0;
    if(!w || !w->valid || !ident || !bid || !isfinite(target))return false;
    const struct ReplicaMover *a=replica_mover_lookup(w->a,w->count_a,ident,bid);
    if(w->future && target>=w->source_b){
        if(target-w->source_b>=250)return false;
        const struct ReplicaMover *b=replica_mover_lookup(w->b,w->count_b,ident,bid);if(!b)return false;
        out[0]=replica_mover_accel(b->accel);out[1]=replica_mover_angular(b->angular);return true;
    }
    if(!a || (!w->future && target-w->source_a>=250))return false;
    if(w->future && target>=w->source_a){
        const struct ReplicaMover *b=replica_mover_lookup(w->b,w->count_b,ident,bid);
        if(b){
            double s=(target-w->source_a)/(w->source_b-w->source_a);
            out[0]=replica_mover_accel((float)(a->accel+(b->accel-a->accel)*s));
            out[1]=replica_mover_angular((float)(a->angular+(b->angular-a->angular)*s));return true;
        }
        if(target-w->source_a>=250)return false;
    }
    out[0]=replica_mover_accel(a->accel);out[1]=replica_mover_angular(a->angular);return true;
}
static void replica_mover_window_clear(struct ReplicaMoverWindow *w){w->valid=false;w->future=false;w->count_a=w->count_b=0;}
#endif
