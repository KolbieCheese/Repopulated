/* The existing 60 Hz swap deadline, with a bounded kernel wait. Actual
 * scheduler latency can exceed the request; lifetime stats keep that visible. */
#ifndef REPOPULATED_FRAME_PACING_H
#define REPOPULATED_FRAME_PACING_H
#ifndef REPLICA_SCENE_PACING_BEGIN
#define REPLICA_SCENE_PACING_BEGIN() ((void)0)
#define REPLICA_SCENE_PACING_END() ((void)0)
#endif
static LARGE_INTEGER replica_pace_frequency,replica_pace_deadline;
static HANDLE replica_pace_timer;
static volatile LONG64 replica_pace_stats[7];
static volatile LONG replica_pace_last_result;
static DWORD replica_pace_wait_millis(LONGLONG remaining,LONGLONG frequency){
    if(remaining<=0 || frequency<=0)return 0;
    return (DWORD)fmin(25,ceil((double)remaining*1000/(double)frequency)+2);
}
static void replica_pace_max(int index,LONG64 value){
    LONG64 previous=InterlockedCompareExchange64(replica_pace_stats+index,0,0);
    while(value>previous){
        LONG64 seen=InterlockedCompareExchange64(replica_pace_stats+index,value,previous);
        if(seen==previous)break;previous=seen;
    }
}
static void replica_pace_frame(void){
    InterlockedIncrement64(replica_pace_stats);
    if(!replica_pace_frequency.QuadPart){
        if(!QueryPerformanceFrequency(&replica_pace_frequency) || replica_pace_frequency.QuadPart<=0){InterlockedIncrement64(replica_pace_stats+3);return;}
        replica_pace_timer=CreateWaitableTimerExW(NULL,NULL,2,TIMER_ALL_ACCESS);
        if(!replica_pace_timer)InterlockedIncrement64(replica_pace_stats+3);
    }
    LARGE_INTEGER now;if(!QueryPerformanceCounter(&now)){InterlockedIncrement64(replica_pace_stats+3);return;}
    LONGLONG step=replica_pace_frequency.QuadPart/60;
    if(step<=0){InterlockedIncrement64(replica_pace_stats+3);return;}
    if(!replica_pace_deadline.QuadPart || now.QuadPart-replica_pace_deadline.QuadPart>step*4){replica_pace_deadline.QuadPart=now.QuadPart;InterlockedIncrement64(replica_pace_stats+6);}
    if(replica_pace_timer && replica_pace_deadline.QuadPart>now.QuadPart){
        LONGLONG remaining=replica_pace_deadline.QuadPart-now.QuadPart;
        LARGE_INTEGER due;due.QuadPart=-(remaining*10000000/replica_pace_frequency.QuadPart);
        DWORD requested=replica_pace_wait_millis(remaining,replica_pace_frequency.QuadPart);
        if(SetWaitableTimer(replica_pace_timer,&due,0,NULL,NULL,false)){
            LARGE_INTEGER started=now,finished;QueryPerformanceCounter(&started);
            InterlockedIncrement64(replica_pace_stats+1);replica_pace_max(5,requested);
            DWORD result=WaitForSingleObject(replica_pace_timer,requested);
            InterlockedExchange(&replica_pace_last_result,(LONG)result);
            if(result==WAIT_TIMEOUT)InterlockedIncrement64(replica_pace_stats+2);
            else if(result!=WAIT_OBJECT_0)InterlockedIncrement64(replica_pace_stats+3);
            if(QueryPerformanceCounter(&finished))replica_pace_max(4,(LONG64)ceil(fmax(0,(double)(finished.QuadPart-started.QuadPart)*1000000/(double)replica_pace_frequency.QuadPart)));
        }else InterlockedIncrement64(replica_pace_stats+3);
    }
    replica_pace_deadline.QuadPart+=step;
}
__declspec(dllexport) void RepopulatedPaceFrame(void){
    REPLICA_SCENE_PACING_BEGIN();
    replica_pace_frame();
    REPLICA_SCENE_PACING_END();
}
__declspec(dllexport) void RepopulatedNativePaceStats(double *out){
    if(!out)return;
    for(int i=0;i<7;i++)out[i]=(double)InterlockedCompareExchange64(replica_pace_stats+i,0,0);
    out[4]/=1000;
    out[7]=(double)(DWORD)InterlockedCompareExchange(&replica_pace_last_result,0,0);
}
#endif
