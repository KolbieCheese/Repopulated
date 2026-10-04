/* Private fixture diagnostics. Native thrust remains local and unmodified. */
#ifndef REPOPULATED_THRUST_AUDIT_H
#define REPOPULATED_THRUST_AUDIT_H
#define REPLICA_THRUST_AUDIT_STRIDE 28
#define REPLICA_THRUST_AUDIT_CAPACITY 512
struct ReplicaThrustAuditContext {uintptr_t root,block,owner,mover;unsigned ident,block_ident;};
struct ReplicaThrustAuditQueue {
    SRWLOCK lock;
    double rows[REPLICA_THRUST_AUDIT_CAPACITY][REPLICA_THRUST_AUDIT_STRIDE];
    unsigned first,count;
    volatile LONG64 total,dropped;
};
static struct ReplicaThrustAuditQueue replica_thrust_audit_queue={.lock=SRWLOCK_INIT};
static volatile LONG replica_thrust_audit_ident,replica_thrust_audit_only;
static void *volatile replica_thrust_audit_system;
static _Thread_local struct ReplicaThrustAuditContext replica_thrust_audit_context;
struct ReplicaParticleRenderAudit {
    SRWLOCK lock;double row[18];bool ready;
    volatile LONG64 calls,dropped;
};
static struct ReplicaParticleRenderAudit replica_particle_audit={.lock=SRWLOCK_INIT};
static void replica_particle_audit_store(const double *row){
    struct ReplicaParticleRenderAudit *a=&replica_particle_audit;
    if(!TryAcquireSRWLockExclusive(&a->lock)){InterlockedIncrement64(&a->dropped);return;}
    memcpy(a->row,row,sizeof(a->row));a->ready=true;ReleaseSRWLockExclusive(&a->lock);
}
static int replica_particle_audit_read(double *out){
    if(!out)return -1;
    struct ReplicaParticleRenderAudit *a=&replica_particle_audit;
    if(!TryAcquireSRWLockShared(&a->lock))return -7;
    bool ready=a->ready;if(ready){memcpy(out,a->row,sizeof(a->row));out[15]=(double)InterlockedCompareExchange64(&a->calls,0,0);out[16]=(double)InterlockedCompareExchange64(&a->dropped,0,0);}
    ReleaseSRWLockShared(&a->lock);return ready?1:0;
}
static int replica_thrust_audit_configure(unsigned ident,bool audit_only){
    if(ident!=0x70000002)return -1;
    struct ReplicaThrustAuditQueue *q=&replica_thrust_audit_queue;
    if(!TryAcquireSRWLockExclusive(&q->lock))return -7;
    q->first=q->count=0;InterlockedExchange64(&q->total,0);InterlockedExchange64(&q->dropped,0);
    InterlockedExchange(&replica_thrust_audit_only,audit_only?1:0);
    InterlockedExchange(&replica_thrust_audit_ident,(LONG)ident);
    ReleaseSRWLockExclusive(&q->lock);return 1;
}
static void replica_thrust_audit_drop(void){InterlockedIncrement64(&replica_thrust_audit_queue.dropped);}
static void replica_thrust_audit_append(const double row[REPLICA_THRUST_AUDIT_STRIDE]){
    struct ReplicaThrustAuditQueue *q=&replica_thrust_audit_queue;
    if(!TryAcquireSRWLockExclusive(&q->lock)){replica_thrust_audit_drop();return;}
    if(q->count==REPLICA_THRUST_AUDIT_CAPACITY)replica_thrust_audit_drop();
    else {
        unsigned at=(q->first+q->count)%REPLICA_THRUST_AUDIT_CAPACITY;
        memcpy(q->rows[at],row,sizeof(q->rows[at]));q->count++;
    }
    ReleaseSRWLockExclusive(&q->lock);
}
static int replica_thrust_audit_read(double *rows,int capacity,double *stats){
    if(!rows || !stats || capacity<1 || capacity>REPLICA_THRUST_AUDIT_CAPACITY)return -1;
    struct ReplicaThrustAuditQueue *q=&replica_thrust_audit_queue;
    if(!TryAcquireSRWLockExclusive(&q->lock))return -7;
    unsigned count=q->count<(unsigned)capacity?q->count:(unsigned)capacity;
    for(unsigned i=0;i<count;i++)memcpy(rows+(size_t)i*REPLICA_THRUST_AUDIT_STRIDE,q->rows[(q->first+i)%REPLICA_THRUST_AUDIT_CAPACITY],sizeof(q->rows[0]));
    q->first=(q->first+count)%REPLICA_THRUST_AUDIT_CAPACITY;q->count-=count;
    stats[0]=(double)InterlockedCompareExchange64(&q->total,0,0);
    stats[1]=(double)InterlockedCompareExchange64(&q->dropped,0,0);
    stats[2]=q->count;stats[3]=REPLICA_THRUST_AUDIT_STRIDE;
    ReleaseSRWLockExclusive(&q->lock);return (int)count;
}
#endif
