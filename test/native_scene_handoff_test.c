/* Execute the production fair handoff state machine with deterministic event
 * timing and claim/abandon races. No game process or engine lock is used. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
static DWORD fake_thread=1;
static double fake_time;
static unsigned fatal_calls,wait_calls,maximum_requested;
static int wait_mode,race_claim;
static int replica_scene_end(void *zone,int committed);
static int replica_scene_try_begin(void *zone);
static DWORD fake_thread_id(void){return fake_thread;}
static double replica_now_millis(void){return fake_time;}
static LONG native_cas(volatile LONG *at,LONG value,LONG expected){return InterlockedCompareExchange(at,value,expected);}
static LONG fake_cas(volatile LONG *at,LONG value,LONG expected);
static DWORD fake_wait(HANDLE event,DWORD delay){
    (void)event;wait_calls++;if(delay>maximum_requested)maximum_requested=delay;
    if(wait_mode==1 || wait_mode==3){
        int mode=wait_mode;fake_time+=.25;fake_thread=1;
        if(replica_scene_try_begin((void*)1)!=1)abort();
        if(mode==3 && replica_scene_end((void*)1,1)!=1)abort();
        fake_thread=2;wait_mode=mode==1?2:0;return WAIT_OBJECT_0;
    }
    if(wait_mode==2){
        fake_time+=.5;fake_thread=1;if(replica_scene_end((void*)1,1)!=1)abort();
        fake_thread=2;wait_mode=0;return WAIT_OBJECT_0;
    }
    if(wait_mode==4){wait_mode=0;return WAIT_OBJECT_0;} /* stale commit signal */
    if(wait_mode==5){wait_mode=0;return WAIT_FAILED;}
    fake_time+=delay;return WAIT_TIMEOUT;
}
#define GetCurrentThreadId fake_thread_id
#define WaitForSingleObject fake_wait
#define InterlockedCompareExchange fake_cas
#define REPLICA_SCENE_FATAL(reason) ((void)(reason),fatal_calls++)
#include "../native/scene_gate.h"
#undef InterlockedCompareExchange
static LONG fake_cas(volatile LONG *at,LONG value,LONG expected){
    if(at==&replica_scene_gate.state && value==REPLICA_SCENE_RENDERING && expected==REPLICA_SCENE_RESERVED && race_claim){
        int mode=race_claim;race_claim=0;fake_thread=1;
        if(replica_scene_try_begin((void*)1)!=1)abort();
        if(mode==2 && replica_scene_end((void*)1,1)!=1)abort();
        fake_thread=2;if(mode==1)wait_mode=2;
    }
    return native_cas(at,value,expected);
}
static void check(bool good,const char *name){if(!good){fprintf(stderr,"Scene handoff: %s\n",name);exit(1);}}
static void reset(void){
    if(replica_scene_gate.committed)CloseHandle(replica_scene_gate.committed);
    memset(&replica_scene_gate,0,sizeof(replica_scene_gate));fake_thread=1;fake_time=0;
    fatal_calls=wait_calls=maximum_requested=0;wait_mode=race_claim=0;
}
static void configured_request(void){
    reset();check(replica_scene_configure((void*)1)==1,"configure owner");
    check(replica_scene_request((void*)1,1)==1,"request while rendering");
    replica_scene_idle_begin();check(replica_scene_gate.state==REPLICA_SCENE_RESERVED,"next completed frame reserves");
}
int main(void){
    double stats[6],gate_stats[8];
    reset();check(replica_scene_request((void*)1,1)==-1,"disabled request rejected");
    replica_scene_idle_begin();replica_scene_idle_end();check(!replica_scene_gate.state,"bootstrap stays disabled");
    check(replica_scene_configure((void*)1)==1,"configure owner");
    check(replica_scene_request((void*)2,1)==-1 && replica_scene_request((void*)1,2)==-1,"invalid zone/pending rejected");
    fake_thread=2;check(replica_scene_request((void*)1,1)==-1,"foreign owner cannot request");fake_thread=1;
    check(!replica_scene_gate.request && !replica_scene_gate.requests,"unauthorized requests are inert");
    check(replica_scene_request((void*)1,1)==1 && replica_scene_request((void*)1,1)==1,"request coalesces");
    check(replica_scene_gate.requests==1 && replica_scene_try_begin((void*)1)==0 && !wait_calls,"request never waits on active draw");
    replica_scene_idle_begin();fake_thread=2;check(replica_scene_try_begin((void*)1)==-1,"foreign source cannot claim reservation");fake_thread=1;
    check(replica_scene_try_begin((void*)1)==1 && !replica_scene_gate.request && replica_scene_gate.claims==1,"owner claims reserved without waiting");
    fake_time=.5;check(replica_scene_end((void*)1,1)==1,"claim commits");fake_thread=2;replica_scene_idle_end();
    check(replica_scene_gate.state==REPLICA_SCENE_RENDERING && !fatal_calls,"committed reservation closes safely");
    replica_scene_handoff_stats(stats);check(stats[0]==1 && stats[1]==1 && stats[2]==1 && !stats[3] && !stats[5],"request/claim diagnostics");
    configured_request();fake_thread=2;replica_scene_idle_end();
    replica_scene_handoff_stats(stats);replica_scene_stats(gate_stats);
    check(stats[3]==1 && stats[4]==2 && stats[5]==1 && maximum_requested==2,"unclaimed 2ms budget abandons and retains request");
    check(replica_scene_gate.state==REPLICA_SCENE_RENDERING && !fatal_calls && !gate_stats[6],"reservation miss is not fatal transaction timeout");
    replica_scene_idle_begin();fake_thread=1;check(replica_scene_try_begin((void*)1)==1,"request survives miss until later claim");
    check(replica_scene_end((void*)1,1)==1,"later claim commits");
    configured_request();check(replica_scene_request((void*)1,0)==1,"true queue reset clears request");
    fake_thread=2;replica_scene_idle_end();check(!wait_calls && replica_scene_gate.state==REPLICA_SCENE_RENDERING,"reset abandons without waiting or writing");
    configured_request();fake_thread=2;wait_mode=4;replica_scene_idle_end();
    check(wait_calls==2 && fake_time==2 && !fatal_calls && replica_scene_gate.abandoned==1,"stale signal retries state and preserves total budget");
    configured_request();fake_thread=2;wait_mode=1;replica_scene_idle_end();
    check(wait_calls==2 && !fatal_calls && replica_scene_gate.claims==1 && replica_scene_gate.transactions==1 && replica_scene_gate.state==REPLICA_SCENE_RENDERING,"claim wake hands over to normal commit wait");
    replica_scene_handoff_stats(stats);check(stats[4]==.25 && !stats[3],"reservation time excludes committed mutation wait");
    configured_request();fake_thread=2;wait_mode=3;replica_scene_idle_end();
    check(wait_calls==1 && !fatal_calls && replica_scene_gate.state==REPLICA_SCENE_RENDERING,"claim and commit before wake closes idle");
    for(int race=1;race<=2;race++){
        configured_request();fake_thread=2;race_claim=race;replica_scene_idle_end();
        check(!fatal_calls && replica_scene_gate.claims==1 && !replica_scene_gate.abandoned && replica_scene_gate.transactions==1 &&
              replica_scene_gate.state==REPLICA_SCENE_RENDERING,"source winning abandonment CAS race must commit");
    }
    configured_request();fake_thread=2;wait_mode=5;replica_scene_idle_end();
    check(!fatal_calls && replica_scene_gate.abandoned==1 && replica_scene_gate.state==REPLICA_SCENE_RENDERING,"failed reservation event can safely abandon unclaimed state");
    /* Successful ordinary admission also serves a pending request. */
    reset();check(replica_scene_configure((void*)1)==1,"ordinary configure");replica_scene_idle_begin();
    check(replica_scene_request((void*)1,1)==1 && replica_scene_try_begin((void*)1)==1,"ordinary idle serves pending work");
    check(!replica_scene_gate.request && !replica_scene_gate.claims,"ordinary claim clears request without reserved counter");
    check(replica_scene_end((void*)1,1)==1,"ordinary commit");
    configured_request();check(replica_scene_try_begin((void*)1)==1,"stalled reserved writer claims");
    fake_thread=2;replica_scene_idle_end();replica_scene_stats(gate_stats);
    check(fatal_calls==1 && gate_stats[6]==1 && fake_time==100 && replica_scene_gate.state==REPLICA_SCENE_MUTATING,"claimed writer retains 100ms fail-closed deadline");
    fake_thread=1;check(replica_scene_end((void*)1,0)==-1 && fatal_calls==2 && replica_scene_gate.state==REPLICA_SCENE_MUTATING,"uncommitted reserved writer never releases drawing");
    reset();puts("scene handoff: authorized request, 2ms nonfatal reservation, claim races and 100ms committed-writer safety passed");
    return 0;
}
