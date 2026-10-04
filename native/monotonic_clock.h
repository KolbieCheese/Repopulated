/* One high-resolution monotonic millisecond clock for native presentation,
 * correction and wire sampling. Direct QPC time shares one epoch between
 * processes on the same PC; source/receipt offset handles different PCs. */
#ifndef REPOPULATED_MONOTONIC_CLOCK_H
#define REPOPULATED_MONOTONIC_CLOCK_H
static INIT_ONCE replica_clock_once=INIT_ONCE_STATIC_INIT;
static LARGE_INTEGER replica_clock_frequency;
static BOOL CALLBACK replica_clock_initialize(PINIT_ONCE once,PVOID parameter,PVOID *context){
    (void)once;(void)parameter;(void)context;
    if(!QueryPerformanceFrequency(&replica_clock_frequency) || replica_clock_frequency.QuadPart<=0)replica_clock_frequency.QuadPart=0;
    return TRUE;
}
static double replica_now_millis(void){
    InitOnceExecuteOnce(&replica_clock_once,replica_clock_initialize,NULL,NULL);
    LARGE_INTEGER now;
    if(replica_clock_frequency.QuadPart>0 && QueryPerformanceCounter(&now))
        return (double)now.QuadPart*1000/(double)replica_clock_frequency.QuadPart;
    return (double)GetTickCount64();
}
__declspec(dllexport) double RepopulatedMonotonicMillis(void){return replica_now_millis();}
#endif
