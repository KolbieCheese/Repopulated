#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
#include "../native/thrust_audit.h"
static void emit(double sample){
    double row[REPLICA_THRUST_AUDIT_STRIDE];
    for(int i=0;i<REPLICA_THRUST_AUDIT_STRIDE;i++)row[i]=sample*100+i;
    InterlockedIncrement64(&replica_thrust_audit_queue.total);replica_thrust_audit_append(row);
}
static DWORD WINAPI check_thread_context(void *unused){
    (void)unused;
    assert(!replica_thrust_audit_context.root);
    replica_thrust_audit_context.root=222;
    assert(replica_thrust_audit_context.root==222);
    return 0;
}
int main(void){
    double rows[128*REPLICA_THRUST_AUDIT_STRIDE],stats[4]={-1,-1,-1,-1};
    assert(replica_thrust_audit_configure(0x70000001,true)==-1);
    assert(!replica_thrust_audit_ident && !replica_thrust_audit_only);
    assert(replica_thrust_audit_configure(0x70000002,true)==1);
    assert(replica_thrust_audit_ident==0x70000002 && replica_thrust_audit_only);
    assert(replica_thrust_audit_read(NULL,1,stats)==-1);
    assert(replica_thrust_audit_read(rows,0,stats)==-1);
    assert(replica_thrust_audit_read(rows,513,stats)==-1);
    assert(replica_thrust_audit_read(rows,1,NULL)==-1);
    assert(stats[0]==-1 && stats[3]==-1);
    for(int i=0;i<520;i++)emit(i);
    assert(replica_thrust_audit_read(rows,128,stats)==128);
    assert(stats[0]==520 && stats[1]==8 && stats[2]==384 && stats[3]==28);
    for(int i=0;i<128;i++)for(int j=0;j<28;j++)assert(rows[i*28+j]==i*100+j);
    /* Drain/append across the ring boundary: newly emitted rows cannot
     * overwrite unread samples or change the immutable payload. */
    for(int i=520;i<648;i++)emit(i);
    for(int batch=1;batch<4;batch++){
        assert(replica_thrust_audit_read(rows,128,stats)==128);
        for(int i=0;i<128;i++)assert(rows[i*28]==(batch*128+i)*100);
    }
    assert(replica_thrust_audit_read(rows,128,stats)==128);
    for(int i=0;i<128;i++)assert(rows[i*28]==(520+i)*100);
    assert(!stats[2] && stats[0]==648 && stats[1]==8);
    AcquireSRWLockExclusive(&replica_thrust_audit_queue.lock);
    emit(999);assert(replica_thrust_audit_read(rows,1,stats)==-7);
    assert(replica_thrust_audit_configure(0x70000002,false)==-7 && replica_thrust_audit_only);
    ReleaseSRWLockExclusive(&replica_thrust_audit_queue.lock);
    assert(replica_thrust_audit_read(rows,128,stats)==0 && stats[0]==649 && stats[1]==9);
    assert(replica_thrust_audit_configure(0x70000002,false)==1 && !replica_thrust_audit_only);
    assert(replica_thrust_audit_read(rows,128,stats)==0 && !stats[0] && !stats[1]);
    /* Concurrent native mover callbacks must not inherit another thread's
     * nozzle/root association. The production context uses compiler TLS. */
    replica_thrust_audit_context.root=111;
    HANDLE worker=CreateThread(NULL,0,check_thread_context,NULL,0,NULL);assert(worker);
    assert(WaitForSingleObject(worker,1000)==WAIT_OBJECT_0);CloseHandle(worker);
    assert(replica_thrust_audit_context.root==111);
    /* The render snapshot never holds its diagnostic lock through a native
     * draw; readers observe one complete immutable row or skip contention. */
    double render[18],copied[18];for(int i=0;i<18;i++)render[i]=i+.25;
    assert(replica_particle_audit_read(copied)==0 && replica_particle_audit_read(NULL)==-1);
    InterlockedIncrement64(&replica_particle_audit.calls);replica_particle_audit_store(render);
    assert(replica_particle_audit_read(copied)==1 && copied[0]==.25 && copied[17]==17.25 && copied[15]==1 && copied[16]==0);
    AcquireSRWLockExclusive(&replica_particle_audit.lock);
    assert(replica_particle_audit_read(copied)==-7);render[0]=999;replica_particle_audit_store(render);
    ReleaseSRWLockExclusive(&replica_particle_audit.lock);
    assert(replica_particle_audit_read(copied)==1 && copied[0]==.25 && copied[16]==1);
    puts("thrust audit: bounded immutable FIFO, overflow/contention drops, exact row stride and thread-local association passed");
    return 0;
}
