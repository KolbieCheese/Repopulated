#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static LONGLONG fake_now=600000,fake_due;
static DWORD fake_wait_result=WAIT_OBJECT_0,fake_requested;
static bool fake_arm_ok=true,fake_create_ok=true;
static unsigned fake_create_calls,fake_wait_calls;
static BOOL fake_frequency(LARGE_INTEGER *out){out->QuadPart=60000;return TRUE;}
static BOOL fake_counter(LARGE_INTEGER *out){out->QuadPart=fake_now;return TRUE;}
static HANDLE fake_create(LPSECURITY_ATTRIBUTES attributes,LPCWSTR name,DWORD flags,DWORD access){
    (void)attributes;(void)name;(void)flags;(void)access;fake_create_calls++;
    return fake_create_ok?(HANDLE)(uintptr_t)1:NULL;
}
static BOOL fake_arm(HANDLE timer,const LARGE_INTEGER *due,LONG period,PTIMERAPCROUTINE callback,LPVOID argument,BOOL resume){
    (void)timer;(void)period;(void)callback;(void)argument;(void)resume;fake_due=due->QuadPart;return fake_arm_ok;
}
static DWORD fake_wait(HANDLE timer,DWORD requested){
    (void)timer;fake_wait_calls++;fake_requested=requested;
    if(fake_wait_result==WAIT_TIMEOUT)fake_now+=(LONGLONG)requested*60;
    else if(fake_wait_result==WAIT_OBJECT_0)fake_now+=(LONGLONG)ceil((double)-fake_due*60000/10000000);
    return fake_wait_result;
}
#define QueryPerformanceFrequency fake_frequency
#define QueryPerformanceCounter fake_counter
#define CreateWaitableTimerExW fake_create
#define SetWaitableTimer fake_arm
#define WaitForSingleObject fake_wait
#include "../native/frame_pacing.h"
#undef QueryPerformanceFrequency
#undef QueryPerformanceCounter
#undef CreateWaitableTimerExW
#undef SetWaitableTimer
#undef WaitForSingleObject
static void check(bool condition,const char *name){if(!condition){fprintf(stderr,"Frame pacing: %s\n",name);exit(1);}}
int main(void){
    check(replica_pace_wait_millis(1000,60000)==19,"fractional deadline rounds up with2ms margin");
    check(replica_pace_wait_millis(1,60000)==3,"submillisecond deadline remains bounded");
    check(replica_pace_wait_millis(6000,60000)==25,"lost or distant deadline capped at25ms");
    check(replica_pace_wait_millis(0,60000)==0,"elapsed deadline does not wait");
    RepopulatedPaceFrame();LONGLONG first=fake_now;
    for(int i=0;i<60;i++)RepopulatedPaceFrame();
    double stats[8];RepopulatedNativePaceStats(stats);
    check(fake_now-first==60000,"sixty normal deadlines retain exact one-second cadence");
    check(stats[0]==61 && stats[1]==60 && stats[2]==0 && stats[3]==0,"normal wait lifetime counters");
    check(stats[5]==19 && stats[6]==1,"normal requested wait and initial deadline reset");
    check(stats[4]>=16.666 && stats[4]<16.669,"actual wait uses high-resolution counter");
    LONGLONG previous_deadline=replica_pace_deadline.QuadPart;
    fake_now=previous_deadline+500;RepopulatedPaceFrame();
    check(replica_pace_deadline.QuadPart==previous_deadline+1000,"small late frame preserves deadline phase");
    previous_deadline=replica_pace_deadline.QuadPart;fake_now=previous_deadline+4000;RepopulatedPaceFrame();
    check(replica_pace_deadline.QuadPart==previous_deadline+1000,"four-step catchup does not reset");
    fake_now=replica_pace_deadline.QuadPart+4001;RepopulatedPaceFrame();
    check(replica_pace_deadline.QuadPart==fake_now+1000,"greater-than-four-step lag resets");
    fake_wait_result=WAIT_TIMEOUT;RepopulatedPaceFrame();RepopulatedNativePaceStats(stats);
    check(stats[2]==1 && stats[7]==WAIT_TIMEOUT,"timeout is visible in lifetime diagnostics");
    replica_pace_deadline.QuadPart=fake_now+6000;RepopulatedPaceFrame();RepopulatedNativePaceStats(stats);
    check(fake_requested==25 && stats[5]==25 && stats[4]==25,"missing signal never requests old100ms timeout");
    fake_wait_result=WAIT_FAILED;RepopulatedPaceFrame();RepopulatedNativePaceStats(stats);
    check(stats[3]==1 && stats[7]==(double)(DWORD)WAIT_FAILED,"failed wait result retains unsigned Win32 code");
    fake_arm_ok=false;unsigned waited=fake_wait_calls;RepopulatedPaceFrame();RepopulatedNativePaceStats(stats);
    check(stats[3]==2 && fake_wait_calls==waited,"arming failure does not enter a wait");
    memset((void*)replica_pace_stats,0,sizeof(replica_pace_stats));
    replica_pace_frequency.QuadPart=0;replica_pace_deadline.QuadPart=0;replica_pace_timer=NULL;
    fake_create_ok=false;unsigned created=fake_create_calls;RepopulatedPaceFrame();RepopulatedPaceFrame();RepopulatedNativePaceStats(stats);
    check(stats[3]==1 && fake_create_calls==created+1,"unavailable timer is counted once without repeated creation");
    /* Check the actual host kernel timer API as well as controlled timeout
     * paths. This does not start the game or measure compositor cadence. */
    HANDLE timer=CreateWaitableTimerExW(NULL,NULL,2,TIMER_ALL_ACCESS);
    check(timer!=NULL,"host high-resolution timer is available");
    LARGE_INTEGER due;due.QuadPart=-10000;
    check(SetWaitableTimer(timer,&due,0,NULL,NULL,FALSE),"actual one-millisecond timer arms");
    DWORD result=WaitForSingleObject(timer,25);CloseHandle(timer);
    check(result==WAIT_OBJECT_0,"actual bounded timer wait completes");
    puts("Native frame pacing:60Hz cadence,25ms bound,catchup,timeout/failure diagnostics and real timer passed.");
    return 0;
}
