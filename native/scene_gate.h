/* A private campaign render-idle transaction. No engine locks are acquired.
 * The source thread never waits: it defers work until the pre-swap idle
 * window, through swap and post-swap deferred work. Rendering waits before
 * the next Render enters native state/player locks. Old pending motion can
 * request a 2ms-budget reserved opportunity; abandoning an unclaimed slot
 * makes no writes and is nonfatal. A stalled/failed claimed transaction still
 * stops this process before another draw. */
#ifndef REPOPULATED_SCENE_GATE_H
#define REPOPULATED_SCENE_GATE_H
enum { REPLICA_SCENE_DISABLED=0,REPLICA_SCENE_RENDERING=1,
       REPLICA_SCENE_IDLE=2,REPLICA_SCENE_MUTATING=3,REPLICA_SCENE_RESERVED=4 };
struct ReplicaSceneGate {
    volatile LONG state;
    void *zone;DWORD owner;HANDLE committed;
    double started;
    volatile LONG64 acquired,deferred,transactions,max_transaction_us;
    volatile LONG64 render_waits,max_render_wait_us,timeouts;
    volatile LONG request;
    volatile LONG64 requests,reservations,claims,abandoned,max_reservation_wait_us;
};
static struct ReplicaSceneGate replica_scene_gate;
static void replica_scene_max(volatile LONG64 *counter,LONG64 value){
    LONG64 old=InterlockedCompareExchange64(counter,0,0);
    while(value>old){LONG64 seen=InterlockedCompareExchange64(counter,value,old);if(seen==old)break;old=seen;}
}
static void replica_scene_gate_fatal(const char *reason){
    /* Use a separate Win32 handle, never a FILE/engine mutex that the stopped
     * source thread might own. The harness supplied this private log path. */
    char path[MAX_PATH*4];DWORD length=GetEnvironmentVariableA("REPOPULATED_DIAGNOSTIC_LOG",path,sizeof(path));
    if(length && length<sizeof(path)){
        HANDLE log=CreateFileA(path,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
        if(log!=INVALID_HANDLE_VALUE){char text[256];DWORD written;
            int size=snprintf(text,sizeof(text),"{\"type\":\"scene-gate-fatal\",\"reason\":\"%s\",\"state\":%ld,\"thread\":%lu}\n",reason,
                InterlockedCompareExchange(&replica_scene_gate.state,0,0),GetCurrentThreadId());
            if(size>0 && (size_t)size<sizeof(text))WriteFile(log,text,(DWORD)size,&written,NULL);CloseHandle(log);
        }
    }
    TerminateProcess(GetCurrentProcess(),70);
    /* TerminateProcess cannot return to drawing if the OS rejects it. */
    ExitProcess(70);
}
#ifndef REPLICA_SCENE_FATAL
#define REPLICA_SCENE_FATAL(reason) replica_scene_gate_fatal(reason)
#endif
static int replica_scene_configure(void *zone){
    if(!zone)return -1;
    LONG state=InterlockedCompareExchange(&replica_scene_gate.state,0,0);
    if(state)return replica_scene_gate.zone==zone && replica_scene_gate.owner==GetCurrentThreadId()?1:-1;
    HANDLE committed=CreateEventW(NULL,FALSE,FALSE,NULL);if(!committed)return -1;
    replica_scene_gate.zone=zone;replica_scene_gate.owner=GetCurrentThreadId();
    replica_scene_gate.committed=committed;
    InterlockedExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING);return 1;
}
static int replica_scene_try_begin(void *zone){
    if(!zone || replica_scene_gate.zone!=zone || replica_scene_gate.owner!=GetCurrentThreadId() ||
       !InterlockedCompareExchange(&replica_scene_gate.state,0,0))return -1;
    LONG before=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_MUTATING,REPLICA_SCENE_IDLE);
    bool reserved=before==REPLICA_SCENE_RESERVED;
    if(before!=REPLICA_SCENE_IDLE && (!reserved ||
       InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_MUTATING,REPLICA_SCENE_RESERVED)!=REPLICA_SCENE_RESERVED)){
        InterlockedIncrement64(&replica_scene_gate.deferred);return 0;
    }
    replica_scene_gate.started=replica_now_millis();InterlockedIncrement64(&replica_scene_gate.acquired);
    InterlockedExchange(&replica_scene_gate.request,0);
    if(reserved){InterlockedIncrement64(&replica_scene_gate.claims);SetEvent(replica_scene_gate.committed);}
    return 1;
}
static int replica_scene_request(void *zone,int pending){
    if(!zone || replica_scene_gate.zone!=zone || replica_scene_gate.owner!=GetCurrentThreadId() ||
       (pending!=0 && pending!=1) || !InterlockedCompareExchange(&replica_scene_gate.state,0,0))return -1;
    if(pending){
        if(!InterlockedExchange(&replica_scene_gate.request,1))InterlockedIncrement64(&replica_scene_gate.requests);
    }else{
        InterlockedExchange(&replica_scene_gate.request,0);
        /* Wake an unclaimed reservation so a true queue reset does not add
         * a needless wait. State, not the event signal, grants writer access. */
        SetEvent(replica_scene_gate.committed);
    }
    return 1;
}
static int replica_scene_end(void *zone,int committed){
    if(zone!=replica_scene_gate.zone || replica_scene_gate.owner!=GetCurrentThreadId() ||
       InterlockedCompareExchange(&replica_scene_gate.state,0,0)!=REPLICA_SCENE_MUTATING)return -1;
    if(!committed){REPLICA_SCENE_FATAL("uncommitted native scene transaction");return -1;}
    double elapsed=fmax(0,replica_now_millis()-replica_scene_gate.started);
    replica_scene_max(&replica_scene_gate.max_transaction_us,(LONG64)ceil(elapsed*1000));
    InterlockedIncrement64(&replica_scene_gate.transactions);
    InterlockedExchange(&replica_scene_gate.state,REPLICA_SCENE_IDLE);
    SetEvent(replica_scene_gate.committed);return 1;
}
static void replica_scene_idle_begin(void){
    LONG idle=InterlockedCompareExchange(&replica_scene_gate.request,0,0)?REPLICA_SCENE_RESERVED:REPLICA_SCENE_IDLE;
    if(InterlockedCompareExchange(&replica_scene_gate.state,idle,REPLICA_SCENE_RENDERING)==REPLICA_SCENE_RENDERING && idle==REPLICA_SCENE_RESERVED)
        InterlockedIncrement64(&replica_scene_gate.reservations);
}
static void replica_scene_idle_end(void){
    LONG state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_IDLE);
    if(state==REPLICA_SCENE_RESERVED){
        double reserved_started=replica_now_millis();
        for(;;){
            double elapsed=fmax(0,replica_now_millis()-reserved_started);
            if(elapsed>=2 || !InterlockedCompareExchange(&replica_scene_gate.request,0,0)){
                /* An unclaimed reservation has made no object writes. Only
                 * a winning RESERVED->RENDERING CAS may abandon it. A source
                 * that wins the race owns MUTATING and must commit normally. */
                state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_RESERVED);
                if(state==REPLICA_SCENE_RESERVED){
                    InterlockedIncrement64(&replica_scene_gate.abandoned);
                    replica_scene_max(&replica_scene_gate.max_reservation_wait_us,(LONG64)ceil(elapsed*1000));return;
                }
                break;
            }
            DWORD remaining=(DWORD)fmax(1,ceil(2-elapsed));
            DWORD waited=WaitForSingleObject(replica_scene_gate.committed,remaining);
            state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_IDLE);
            if(state!=REPLICA_SCENE_RESERVED)break;
            if(waited!=WAIT_OBJECT_0 && waited!=WAIT_TIMEOUT){
                state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_RESERVED);
                if(state==REPLICA_SCENE_RESERVED){
                    InterlockedIncrement64(&replica_scene_gate.abandoned);
                    replica_scene_max(&replica_scene_gate.max_reservation_wait_us,(LONG64)ceil(fmax(0,replica_now_millis()-reserved_started)*1000));return;
                }
                break;
            }
        }
        replica_scene_max(&replica_scene_gate.max_reservation_wait_us,(LONG64)ceil(fmax(0,replica_now_millis()-reserved_started)*1000));
        /* A claim and commit may both finish before the renderer wakes. */
        if(state==REPLICA_SCENE_IDLE){
            state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_IDLE);
            if(state==REPLICA_SCENE_IDLE)return;
        }
    }
    if(state!=REPLICA_SCENE_MUTATING)return;
    double started=replica_now_millis();InterlockedIncrement64(&replica_scene_gate.render_waits);
    for(;;){
        state=InterlockedCompareExchange(&replica_scene_gate.state,REPLICA_SCENE_RENDERING,REPLICA_SCENE_IDLE);
        if(state==REPLICA_SCENE_IDLE){
            replica_scene_max(&replica_scene_gate.max_render_wait_us,(LONG64)ceil(fmax(0,replica_now_millis()-started)*1000));return;
        }
        if(state!=REPLICA_SCENE_MUTATING){REPLICA_SCENE_FATAL("invalid render-idle transaction state");return;}
        double elapsed=replica_now_millis()-started;
        if(elapsed>=100){
            InterlockedIncrement64(&replica_scene_gate.timeouts);REPLICA_SCENE_FATAL("native scene transaction exceeded 100 ms render wait");return;
        }
        /* Wake on the source's commit instead of adding a sleep quantum to
         * every overlapping frame. A stale earlier signal merely retries CAS;
         * the source never waits for this renderer to acknowledge it. */
        DWORD remaining=(DWORD)fmax(1,ceil(100-elapsed));
        DWORD waited=WaitForSingleObject(replica_scene_gate.committed,remaining);
        if(waited!=WAIT_OBJECT_0 && waited!=WAIT_TIMEOUT){REPLICA_SCENE_FATAL("native scene commit event wait failed");return;}
    }
}
static void replica_scene_handoff_stats(double *out){
    if(!out)return;
    out[0]=(double)InterlockedCompareExchange64(&replica_scene_gate.requests,0,0);
    out[1]=(double)InterlockedCompareExchange64(&replica_scene_gate.reservations,0,0);
    out[2]=(double)InterlockedCompareExchange64(&replica_scene_gate.claims,0,0);
    out[3]=(double)InterlockedCompareExchange64(&replica_scene_gate.abandoned,0,0);
    out[4]=(double)InterlockedCompareExchange64(&replica_scene_gate.max_reservation_wait_us,0,0)/1000;
    out[5]=(double)InterlockedCompareExchange(&replica_scene_gate.request,0,0);
}
static void replica_scene_stats(double *out){
    if(!out)return;
    out[0]=(double)InterlockedCompareExchange64(&replica_scene_gate.acquired,0,0);
    out[1]=(double)InterlockedCompareExchange64(&replica_scene_gate.deferred,0,0);
    out[2]=(double)InterlockedCompareExchange64(&replica_scene_gate.transactions,0,0);
    out[3]=(double)InterlockedCompareExchange64(&replica_scene_gate.max_transaction_us,0,0)/1000;
    out[4]=(double)InterlockedCompareExchange64(&replica_scene_gate.render_waits,0,0);
    out[5]=(double)InterlockedCompareExchange64(&replica_scene_gate.max_render_wait_us,0,0)/1000;
    out[6]=(double)InterlockedCompareExchange64(&replica_scene_gate.timeouts,0,0);
    out[7]=(double)InterlockedCompareExchange(&replica_scene_gate.state,0,0);
}
#endif
