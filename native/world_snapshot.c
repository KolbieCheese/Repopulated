/* Included by diagnostic.c. Exact-build, simulation-thread research only. */
struct WorldString { union { char text[16]; const char *pointer; } buffer; size_t size,capacity; };
typedef void* (*WorldSerialize)(void*,void*);
typedef void (*WorldDestroy)(void*);
static char world_load_message[4096];
static float world_visual_center[2];
static bool replica_is_predicted(unsigned ident);
static bool replica_prediction_active(uintptr_t cluster);
static void prediction_reset(void);
static void prediction_reconcile(unsigned ident,float *position,float *velocity,float *angle,float *angular);
#include "replica_timeline.h"
/* Vanilla renders on a different thread from GameZone::Update. Protect only
 * our cache here: acquiring game locks from intercepted native callbacks can
 * invert the game's existing lock order. Rendering and FX must never wait
 * behind a mutation that can call back into the game/JS runtime. */
static INIT_ONCE replica_sync_once=INIT_ONCE_STATIC_INIT;
static CRITICAL_SECTION replica_cache_mutex;
static bool replica_sync_ready;
static volatile LONG64 replica_presentation_guard_stats[3];
static BOOL CALLBACK replica_sync_init(PINIT_ONCE once,PVOID parameter,PVOID *context){
    (void)once;(void)parameter;(void)context;
    InitializeCriticalSection(&replica_cache_mutex);
    replica_sync_ready=true;
    return TRUE;
}
static bool replica_cache_enter(void){
    InitOnceExecuteOnce(&replica_sync_once,replica_sync_init,NULL,NULL);
    if(!replica_sync_ready)return false;
    EnterCriticalSection(&replica_cache_mutex);return true;
}
static void replica_cache_leave(void){LeaveCriticalSection(&replica_cache_mutex);}
static bool replica_guard_enter(void *zone){
    return zone && replica_cache_enter();
}
static void replica_guard_leave(void *zone){
    (void)zone;
    replica_cache_leave();
}
static bool replica_cache_try_enter(void){
    InitOnceExecuteOnce(&replica_sync_once,replica_sync_init,NULL,NULL);
    return replica_sync_ready && TryEnterCriticalSection(&replica_cache_mutex);
}
static bool replica_guard_try_enter(void *zone){return zone && replica_cache_try_enter();}
static bool replica_roots_copy(void *zone,uintptr_t *roots,size_t *count){
    uintptr_t vector[2];bool ok=read_mem((char*)zone+0x188,vector,16) && vector[1]>=vector[0] &&
        !((vector[1]-vector[0])%8) && vector[1]-vector[0]<=4096*8;
    if(ok){*count=(vector[1]-vector[0])/8;ok=!*count || read_mem((void*)vector[0],roots,*count*8);}
    return ok;
}
#include "replica_replacement.h"
static struct ReplicaSmoothing *replica_history(unsigned ident,bool create);
static struct ReplicaSmoothing replica_smoothing[4096];
#include "emitter_curve.h"
static struct ReplicaEmitterPublication replica_emitter_publications[4096];
static volatile LONG64 replica_emitter_counts[5];
static _Thread_local int replica_emitter_hint;
static unsigned replica_motion_sequence;
static double replica_motion_sampled_at,replica_motion_source_at,replica_motion_clock_offset;
static struct ReplicaSimulationClock replica_simulation_clock;
static bool replica_motion_clock_ready;
static double replica_presentation_delay,replica_visual_source_at;
static bool replica_visual_clock_ready;
static unsigned long long replica_interpolation_stats[5];
static unsigned long long replica_motion_duplicates,replica_geometry_motion_skips;
static volatile LONG64 replica_replacement_counts[8];
static double replica_max_correction,replica_max_angle_correction;
static uintptr_t replica_motion_trace_pointer[2];
static double replica_motion_trace_observed[2];
static double replica_motion_trace[2][20];
static unsigned long long replica_present_calls;
/* Optional bounded test telemetry. Cache only the two owned ships while the
 * normal presenter/motion loop already visits them; never add a scene scan. */
static int replica_motion_trace_read(void *zone,double *out){
    if(!zone || !out)return -1;
    out[40]=world_visual_center[0];out[41]=world_visual_center[1];
    int valid=0;double now=replica_now_millis();
    for(int i=0;i<2;i++){
        double *r=replica_motion_trace[i];uintptr_t cluster=replica_motion_trace_pointer[i],owner;
        double state[4];float angle[2],render[2],render_angle;
        unsigned expected=0x70000001u+(unsigned)i;
        /* Pools may reuse an out-of-interest root pointer for a different
         * live cluster in the same zone. Owner alone does not prove identity. */
        bool fresh=now-replica_motion_trace_observed[i]<=250;
        if(r[19]){
            struct ReplicaSmoothing *history=replica_history(expected,false);
            fresh=history && history->motion_cluster==cluster && history->timeline.valid && now-history->timeline.sampled_at<=250;
        }
        if(!cluster || !fresh || !read_mem((void*)(cluster+8),&owner,8) || owner!=(uintptr_t)zone ||
           RepopulatedClusterIdent((void*)cluster)!=expected ||
           !read_mem((void*)(cluster+0x30),state,32) || !read_mem((void*)(cluster+0x60),angle,8) ||
           !read_mem((void*)(cluster+0xd8),render,8) || !read_mem((void*)(cluster+0xe8),&render_angle,4)){
            replica_motion_trace_pointer[i]=0;replica_motion_trace_observed[i]=0;
            memset(r,0,20*sizeof(*r));memset(out+i*20,0,20*sizeof(*out));continue;
        }
        r[0]=(double)expected;r[2]=now;
        for(int j=0;j<4;j++)r[4+j]=state[j];r[8]=angle[0];r[9]=angle[1];
        r[16]=render[0];r[17]=render[1];r[18]=render_angle;
        if(!r[19]){r[10]=render[0];r[11]=render[1];r[12]=render_angle;r[13]=state[2];r[14]=state[3];r[15]=angle[1];}
        double *result=out+i*20;memcpy(result,r,20*sizeof(*out));
        /* The visual loader translates by the local native sector center.
         * Report host-world coordinates so paired recordings compare the
         * same ships without an artificial constant translation. */
        for(int j=4;j<=16;j+=6){result[j]-=world_visual_center[0];result[j+1]-=world_visual_center[1];}
        valid++;
    }
    return valid;
}
__declspec(dllexport) int RepopulatedPresentedMotionTrace(void *zone,double *out){
    if(!zone || !out)return -1;
    if(!replica_guard_try_enter(zone)){InterlockedIncrement64(&replica_presentation_guard_stats[1]);return -7;}
    int result=replica_motion_trace_read(zone,out);replica_guard_leave(zone);return result;
}
#include "comparison_focus.h"
__declspec(dllexport) int RepopulatedReadComparisonFocus(void *zone,unsigned ident,double *out){
    initialize();if(!compatible)return -1;
    return replica_comparison_focus(zone,ident,out);
}
__declspec(dllexport) void RepopulatedBeginMotionFrame(unsigned seq,float age,double source_time,double sim_time){
    if(!replica_cache_enter())return;
    double now=replica_now_millis();age=fmaxf(0,fminf(10,age));
    double received=now-(double)age*1000;
    replica_motion_sequence=seq;
    if(isfinite(source_time) && source_time>0){
        double offset=received-source_time;
        if(!replica_motion_clock_ready || offset<replica_motion_clock_offset){replica_motion_clock_offset=offset;replica_motion_clock_ready=true;}
        replica_motion_sampled_at=source_time+replica_motion_clock_offset;
        replica_motion_source_at=source_time;
        if(isfinite(sim_time) && sim_time>=0)replica_simulation_clock_accept(&replica_simulation_clock,source_time,sim_time);
    }else{replica_motion_sampled_at=received;replica_motion_source_at=received-(replica_motion_clock_ready?replica_motion_clock_offset:0);}
    replica_cache_leave();
}
__declspec(dllexport) void RepopulatedConfigurePresentationDelay(float milliseconds){
    if(!replica_cache_enter())return;
    replica_presentation_delay=isfinite(milliseconds)?fmax(0,fmin(200,milliseconds)):0;
    replica_cache_leave();
}
__declspec(dllexport) double RepopulatedViewSourceTime(void){
    if(!replica_cache_try_enter())return -1;
    double source=replica_motion_clock_ready?replica_source_view_time(replica_now_millis(),replica_motion_clock_offset,replica_presentation_delay):-1;
    replica_cache_leave();return source;
}
__declspec(dllexport) void RepopulatedBeginVisualFrame(double source_time){
    if(!replica_cache_enter())return;
    if(replica_motion_clock_ready && isfinite(source_time) && source_time>0){replica_visual_source_at=source_time;replica_visual_clock_ready=true;}
    replica_cache_leave();
}
__declspec(dllexport) int RepopulatedPresentationClockStats(double *out){
    if(!out)return -1;
    if(!replica_cache_try_enter())return -7;
    out[0]=replica_now_millis();out[1]=replica_motion_clock_ready?replica_motion_clock_offset:0;
    out[2]=replica_motion_source_at;out[3]=replica_visual_clock_ready?replica_visual_source_at:0;
    replica_cache_leave();return replica_motion_clock_ready?4:0;
}
static struct ReplicaSmoothing *replica_history(unsigned ident,bool create) {
    struct ReplicaSmoothing *free_slot=NULL,*oldest=&replica_smoothing[0];
    for(int i=0;i<4096;i++){
        struct ReplicaSmoothing *row=&replica_smoothing[i];if(row->ident==ident)return row;
        if(!row->ident && !free_slot)free_slot=row;
        if(row->received<oldest->received)oldest=row;
    }
    if(!create)return NULL;
    struct ReplicaSmoothing *row=free_slot?free_slot:oldest;
    InterlockedExchange(&replica_emitter_publications[row-replica_smoothing].key,0);
    memset(row,0,sizeof(*row));row->ident=ident;return row;
}
static void replica_emitter_publish_history(struct ReplicaSmoothing *history){
    struct ReplicaEmitterCurve curve={.ident=history->ident,.root=history->motion_cluster,
        .clock_offset=replica_motion_clock_ready?replica_motion_clock_offset:0,.delay=replica_presentation_delay,
        .source_rate=replica_motion_sequence?replica_simulation_clock.rate:1,
        .timeline=history->timeline,.interpolation=history->interpolation};
    if(!replica_emitter_publish(&replica_emitter_publications[history-replica_smoothing],&curve))InterlockedIncrement64(&replica_emitter_counts[3]);
}
static bool replica_render_replacement(struct ReplicaSmoothing *history,uintptr_t cluster);
static enum ReplicaRootBinding replica_bind_history(struct ReplicaSmoothing *history,void *zone,uintptr_t cluster){
    unsigned kind=history->replacement.kind;
    bool ticket=history->replacement_pending || history->replacement_initialize;
    enum ReplicaRootBinding result=replica_replacement_bind(history,zone,cluster,read_mem);
    if(result==REPLICA_ROOT_RESET){
        if(ticket && (kind==REPLICA_REPLACEMENT_COMMAND || kind==REPLICA_REPLACEMENT_FRAGMENT))
            InterlockedIncrement64(&replica_replacement_counts[kind==REPLICA_REPLACEMENT_COMMAND?4:5]);
        else InterlockedIncrement64(&replica_replacement_counts[6]);
        InterlockedExchange(&replica_emitter_publications[history-replica_smoothing].key,0);
        replica_interpolation_stats[4]++;
    }else if(result==REPLICA_ROOT_REPLACEMENT){
        InterlockedIncrement64(&replica_replacement_counts[kind==REPLICA_REPLACEMENT_COMMAND?2:3]);
        /* Geometry/update or the presenter can discover the new root before
         * fast replay. Its first render must already use the retained curve. */
        replica_render_replacement(history,cluster);
        replica_emitter_publish_history(history);
    }
    return result;
}
__declspec(dllexport) void RepopulatedReplacementStats(double *out){
    if(out)for(int i=0;i<8;i++)out[i]=(double)InterlockedCompareExchange64(&replica_replacement_counts[i],0,0);
}
static bool replica_emitter_snapshot(unsigned ident,uintptr_t root,struct ReplicaEmitterCurve *out){
    int slot=replica_emitter_hint;
    if((unsigned)InterlockedCompareExchange(&replica_emitter_publications[slot].key,0,0)!=ident){
        slot=-1;
        for(int i=0;i<4096;i++)if((unsigned)InterlockedCompareExchange(&replica_emitter_publications[i].key,0,0)==ident){slot=i;break;}
        if(slot<0)return false;replica_emitter_hint=slot;
    }
    return replica_emitter_copy(&replica_emitter_publications[slot],ident,root,out);
}
static void replica_correction(unsigned ident,float x,float y,float angle) {
    struct ReplicaSmoothing *row=replica_history(ident,true);
    row->dx=row->presented?row->last_x-x:0;row->dy=row->presented?row->last_y-y:0;
    row->da=row->presented?remainderf(row->last_angle-angle,6.283185307f):0;row->received=replica_now_millis();
    if(row->dx*row->dx+row->dy*row->dy>1000000){row->dx=0;row->dy=0;row->da=0;}
}
static bool replica_has_fast_motion(void *zone,unsigned ident,uintptr_t cluster){
    struct ReplicaSmoothing *row=replica_history(ident,false);
    if(row)replica_bind_history(row,zone,cluster);
    /* Once a root has joined the fast channel, geometry owns only its shape.
     * A stalled fast stream freezes at the presentation horizon instead of
     * letting old geometry and motion alternately take over its position. */
    bool ready=row && row->motion_sequence && row->motion_cluster==cluster;
    if(ready)replica_geometry_motion_skips++;
    return ready;
}
__declspec(dllexport) void RepopulatedMotionTimelineStats(double *out){
    out[0]=(double)replica_motion_duplicates;out[1]=(double)replica_geometry_motion_skips;
    out[2]=replica_max_correction;out[3]=replica_max_angle_correction;
}
__declspec(dllexport) double RepopulatedSimulationPresentationRate(void){return replica_simulation_clock.rate;}
__declspec(dllexport) void RepopulatedInterpolationStats(double *out){if(out)for(int i=0;i<5;i++)out[i]=(double)replica_interpolation_stats[i];}
__declspec(dllexport) void RepopulatedPresentationGuardStats(double *out){
    if(!out)return;
    for(int i=0;i<3;i++)out[i]=(double)InterlockedCompareExchange64(&replica_presentation_guard_stats[i],0,0);
}
__declspec(dllexport) const char *RepopulatedWorldLoadMessage(void) { return world_load_message; }
#include "replica_field.h"
static struct ReplicaFieldContext replica_field_context;
static ReplicaFieldGetter replica_field_original;
__declspec(dllexport) void RepopulatedSetFieldOriginal(void *address){memcpy(&replica_field_original,&address,sizeof(address));}
__declspec(dllexport) int RepopulatedConfigureReplicaField(void *console,void *zone){
    initialize();if(!compatible || !replica_field_original)return -1;
    return replica_field_configure(&replica_field_context,console,zone,GetCurrentThreadId(),read_mem);
}
__declspec(dllexport) void *RepopulatedReplicaField(void *console){
    return replica_field_get(&replica_field_context,console,GetCurrentThreadId(),read_mem,replica_field_original);
}
__declspec(dllexport) void RepopulatedReplicaFieldStats(double *out){
    if(out){out[0]=(double)replica_field_context.private_calls;out[1]=(double)replica_field_context.forwarded_calls;out[2]=(double)replica_field_context.rejected_calls;}
}
static bool world_functions(WorldSerialize *serialize,WorldDestroy *destroy) {
    HMODULE game=GetModuleHandleW(NULL);
    const char *name="?toString@BlockCluster@@QEBA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ";
    const unsigned char prefix[]={0x48,0x89,0x5c,0x24,0x08};
    const unsigned char cleanup[]={0x40,0x53,0x48,0x83,0xec,0x20};
    unsigned char actual[sizeof(cleanup)]; uintptr_t address=(uintptr_t)game+0x1e3e0;
    if (!signature(game,name,prefix,sizeof(prefix)) || !read_mem((void*)address,actual,sizeof(actual)) ||
        memcmp(actual,cleanup,sizeof(actual))) return false;
    FARPROC raw=GetProcAddress(game,name); memcpy(serialize,&raw,sizeof(raw));
    memcpy(destroy,&address,sizeof(address)); return true;
}

static int export_world(void *zone,const char *path,unsigned focus,float radius,bool players_only) {
    initialize();
    WorldSerialize serialize; WorldDestroy destroy;
    if (!compatible || !zone || !path || !world_functions(&serialize,&destroy)) return -1;
    uintptr_t vector[3];
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        vector[2]<vector[1] || (vector[1]-vector[0])%8 || vector[2]-vector[0]>4096*8) return -2;
    double center[2]={0};bool found=!focus;
    if(focus) {
        if(!isfinite(radius) || radius<100 || radius>20000) return -8;
        for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
            uintptr_t cluster,parent;
            if(!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
               !read_mem((void*)(cluster+0x178),&parent,8)) return -4;
            if(parent) continue;
            if(RepopulatedClusterIdent((void*)cluster)!=focus) continue;
            if(found || !read_mem((void*)(cluster+0x30),center,sizeof(center)) ||
               !isfinite(center[0]) || !isfinite(center[1])) return -9;
            found=true;
        }
        if(!found) return -10;
    }
    FILE *file=fopen(path,"wb"); if (!file) return -3;
    fputs("offset={0,0}\nradius={9000,9000}\nviewpos={3000,3000,1000}\n",file);
    size_t count=(vector[1]-vector[0])/8,total=0; int roots=0;
    for (size_t i=0;i<count;i++) {
        uintptr_t cluster,parent;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x178),&parent,8)) { roots=-4; break; }
        if (parent) continue;
        if(players_only) {
            unsigned ident=RepopulatedClusterIdent((void*)cluster);
            if(ident!=0x70000001 && ident!=0x70000002) continue;
        }
        if(focus) {
            double position[2];
            if(!read_mem((void*)(cluster+0x30),position,sizeof(position))) {roots=-4;break;}
            double dx=position[0]-center[0],dy=position[1]-center[1];
            if(dx*dx+dy*dy>(double)radius*radius) continue;
        }
        struct WorldString text={0}; serialize((void*)cluster,&text);
        if (!text.size || text.capacity<text.size || text.size>2*1024*1024 || total+text.size>2*1024*1024) {
            destroy(&text); roots=-5; break;
        }
        const char *data=text.capacity>15 ? text.buffer.pointer : text.buffer.text;
        fputs("cluster",file);
        /* Native toString starts with whitespace before its opening brace. */
        if (fwrite(data,1,text.size,file)!=text.size) { destroy(&text); roots=-6; break; }
        fputc('\n',file); total+=text.size; roots++; destroy(&text);
    }
    if (fclose(file)) return -7;
    return roots;
}

__declspec(dllexport) int RepopulatedExportWorld(void *zone,const char *path) {
    return export_world(zone,path,0,0,false);
}

__declspec(dllexport) int RepopulatedExportPlayerShips(void *zone,const char *path) {
    return export_world(zone,path,0,0,true);
}

/* Presentation prediction only: authoritative body poses and physics stay
 * untouched. Freeze after one snapshot interval if the stream stalls. */
typedef void (*ReplicaRenderPose)(void*,uint64_t,float);
static ReplicaRenderPose replica_render_setter(void){
    static ReplicaRenderPose render_pose;
    if(!render_pose){
        HMODULE game=GetModuleHandleW(NULL);
        const char *name="?setRenderPosAngle@Body@@QEAAXU?$tvec2@M$0A@@glm@@M@Z";
        const unsigned char prefix[]={0x48,0x89,0x54,0x24,0x08,0xc5,0xfa,0x10,0x4c,0x24,0x08};
        if(!signature(game,name,prefix,sizeof(prefix)))return NULL;
        FARPROC raw=GetProcAddress(game,name);memcpy(&render_pose,&raw,sizeof(raw));
    }
    return render_pose;
}
static bool replica_render_replacement(struct ReplicaSmoothing *history,uintptr_t cluster){
    if(!history->timeline.valid)return false;
    ReplicaRenderPose render=replica_render_setter();if(!render)return false;
    double now=replica_now_millis();
    double source=replica_source_view_time(now,replica_motion_clock_ready?replica_motion_clock_offset:0,replica_presentation_delay);
    struct ReplicaTimelinePose shown=replica_presentation_delay>0 && history->interpolation.count?
        replica_interpolation_evaluate(&history->interpolation,source):replica_timeline_evaluate(&history->timeline,now-replica_presentation_delay);
    union{float xy[2];uint64_t packed;}visible={.xy={(float)shown.x,(float)shown.y}};
    render((void*)cluster,visible.packed,(float)shown.angle);
    history->last_x=visible.xy[0];history->last_y=visible.xy[1];history->last_angle=(float)shown.angle;history->presented=true;
    return true;
}
static int replica_present(void *zone,float seconds,unsigned pilot,float *pilot_position) {
    initialize();
    if(!compatible || !zone || !pilot_position || !isfinite(seconds) || seconds<0 || seconds>0.25f) return -1;
    ReplicaRenderPose render_pose=replica_render_setter();if(!render_pose)return -2;
    uintptr_t roots[4096];size_t roots_count;
    if(!replica_roots_copy(zone,roots,&roots_count))return -3;
    int count=0;bool found=false;replica_present_calls++;
    double now=replica_now_millis(),presentation_now=now-replica_presentation_delay;
    double source_target=replica_source_view_time(now,replica_motion_clock_ready?replica_motion_clock_offset:0,replica_presentation_delay);
    for(size_t i=0;i<roots_count;i++) {
        uintptr_t cluster,parent;double state[4];float angle,angular;
        cluster=roots[i];if(!read_mem((void*)(cluster+0x178),&parent,8))return -4;
        if(parent) continue;
        if(!read_mem((void*)(cluster+0x30),state,sizeof(state)) ||
           !read_mem((void*)(cluster+0x60),&angle,4) || !read_mem((void*)(cluster+0x64),&angular,4)) return -4;
        if(!isfinite(angle) || !isfinite(angular) || fabsf(angular)>10000) return -5;
        unsigned ident=RepopulatedClusterIdent((void*)cluster);
        bool predicted=replica_is_predicted(ident);
        float elapsed=predicted?0:seconds;
        angle+=angular*elapsed;
        for(int j=0;j<4;j++) if(!isfinite(state[j])) return -5;
        union {float xy[2];uint64_t packed;} position={.xy={(float)(state[0]+state[2]*elapsed),(float)(state[1]+state[3]*elapsed)}};
        struct ReplicaSmoothing *history=replica_history(ident,false);
        if(history)replica_bind_history(history,zone,cluster);
        double shown_vx=state[2],shown_vy=state[3],shown_angular=angular;
        if(history){
            if(history->timeline.valid && !predicted){
                if(replica_presentation_delay>0 && history->interpolation.count){
                    const struct ReplicaInterpolationSample *first=replica_interpolation_sample(&history->interpolation,0),*last=replica_interpolation_sample(&history->interpolation,history->interpolation.count-1);
                    unsigned state=source_target<first->sampled_at?2:source_target<last->sampled_at?0:source_target-last->sampled_at<250?1:3;
                    replica_interpolation_stats[state]++;
                }
                struct ReplicaTimelinePose shown=replica_presentation_delay>0 && history->interpolation.count?
                    replica_interpolation_evaluate(&history->interpolation,source_target):replica_timeline_evaluate(&history->timeline,presentation_now);
                position.xy[0]=(float)shown.x;position.xy[1]=(float)shown.y;angle=(float)shown.angle;
                shown_vx=shown.vx;shown_vy=shown.vy;shown_angular=shown.angular;
            }else {
                float blend=fmaxf(0,1-(float)(presentation_now-history->received)/100.0f);
                position.xy[0]+=history->dx*blend;position.xy[1]+=history->dy*blend;angle+=history->da*blend;
                if(blend>0){shown_vx-=history->dx*10;shown_vy-=history->dy*10;shown_angular-=history->da*10;}
            }
            history->last_x=position.xy[0];history->last_y=position.xy[1];history->last_angle=angle;history->presented=true;
        }
        render_pose((void*)cluster,position.packed,angle);count++;
        if(ident==0x70000001 || ident==0x70000002){
            unsigned n=ident-0x70000001;double *r=replica_motion_trace[n];replica_motion_trace_pointer[n]=cluster;
            r[1]=history?history->motion_sequence:0;r[3]=history && history->timeline.valid?history->timeline.sampled_at:0;
            r[10]=position.xy[0];r[11]=position.xy[1];r[12]=angle;
            r[13]=shown_vx;r[14]=shown_vy;r[15]=shown_angular;r[19]=(double)replica_present_calls;
        }
        if(ident==pilot) {
            pilot_position[0]=position.xy[0];pilot_position[1]=position.xy[1];found=true;
        }
    }
    return found?count:0;
}
__declspec(dllexport) int RepopulatedPresentReplica(void *zone,float seconds,unsigned pilot,float *pilot_position){
    initialize();if(!compatible || !zone)return -6;
    if(!replica_guard_try_enter(zone)){InterlockedIncrement64(&replica_presentation_guard_stats[0]);return -7;}
    int result=replica_present(zone,seconds,pilot,pilot_position);replica_guard_leave(zone);return result;
}
__declspec(dllexport) int RepopulatedExportInterest(void *zone,const char *path,unsigned focus,float radius) {
    return export_world(zone,path,focus,radius,false);
}

static int load_world(void *zone,void *console,const char *path,bool visual,bool replace) {
    initialize();
    WorldSerialize unused; WorldDestroy destroy; uintptr_t console_zone;
    if (!compatible || !zone || !console || !path || strlen(path)>1024 ||
        !read_mem((char*)console+8,&console_zone,8) || console_zone!=(uintptr_t)zone ||
        !world_functions(&unused,&destroy)) return -1;
    HMODULE game=GetModuleHandleW(NULL);
    const char *clear_name="?Clear@GameZone@@QEAAX_N@Z";
    const unsigned char clear_sig[]={0x48,0x89,0x5c,0x24,0x10};
    const unsigned char load_sig[]={0x48,0x8b,0xc4,0x48,0x89,0x58,0x08};
    uintptr_t load_address=(uintptr_t)game+0xbd1a0;
    unsigned char actual[sizeof(load_sig)];
    if (!signature(game,clear_name,clear_sig,sizeof(clear_sig)) ||
        !read_mem((void*)load_address,actual,sizeof(actual)) || memcmp(actual,load_sig,sizeof(actual))) return -2;
    FILE *file=fopen(path,"rb"); if (!file) return -3;
    if (fseek(file,0,SEEK_END)) { fclose(file); return -3; }
    long size=ftell(file); fclose(file); if (size<=0 || size>2*1024*1024) return -3;
    struct WorldString result={0};
    typedef void (*Clear)(void*,bool);
    typedef void* (*Load)(void*,void*,void*,const char*);
    Clear clear; Load load;
    FARPROC raw=GetProcAddress(game,clear_name); memcpy(&clear,&raw,sizeof(raw));
    memcpy(&load,&load_address,sizeof(load));
    /* The handler constructs SaveParser from raw R9 input, not std::string. */
    if(replace) clear(zone,false);
    load(&result,console,NULL,path);
    /* The sandbox loader places sector-relative positions at the current
     * sector center. Our snapshots store absolute positions with offset zero.
     * Read that same field through the inspected helper, then undo translation. */
    typedef void* (*GetField)(void*);
    uintptr_t field_address=(uintptr_t)game+0xcab60; GetField get_field;
    memcpy(&get_field,&field_address,sizeof(get_field));
    void *field=get_field(console); float center[2];
    if (!field || !read_mem((char*)field+0x58,center,sizeof(center)) ||
        !isfinite(center[0]) || !isfinite(center[1])) { destroy(&result); return -6; }
    world_visual_center[0]=visual?center[0]:0;world_visual_center[1]=visual?center[1]:0;
    const char *position_name="?setPos@BlockCluster@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
    const unsigned char position_sig[]={0x48,0x89,0x5c,0x24,0x18};
    if (!signature(game,position_name,position_sig,sizeof(position_sig))) { destroy(&result); return -7; }
    typedef void (*SetPosition)(void*,uint64_t); SetPosition set_position;
    raw=GetProcAddress(game,position_name); memcpy(&set_position,&raw,sizeof(raw));
    uintptr_t clusters[2];
    if (!read_mem((char*)zone+0x188,clusters,sizeof(clusters)) || clusters[1]<clusters[0] ||
        clusters[1]-clusters[0]>4096*8) { destroy(&result); return -8; }
    for (size_t i=0;i<(clusters[1]-clusters[0])/8;i++) {
        uintptr_t cluster,parent; double pos[2];
        if (visual || !read_mem((void*)(clusters[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent ||
            !read_mem((void*)(cluster+0x30),pos,sizeof(pos))) continue;
        union { float xy[2]; uint64_t packed; } target={.xy={(float)(pos[0]-center[0]),(float)(pos[1]-center[1])}};
        set_position((void*)cluster,target.packed);
    }
    bool ok=result.size && result.size<4096;
    if (ok) {
        const char *message=result.capacity>15 ? result.buffer.pointer : result.buffer.text;
        memcpy(world_load_message,message,result.size); world_load_message[result.size]=0;
        ok=strstr(message,"Expanded")!=NULL;
    }
    destroy(&result);
    if (!ok) return -4;
    uintptr_t vector[2]; if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0]) return -5;
    return (int)((vector[1]-vector[0])/8);
}

__declspec(dllexport) int RepopulatedLoadWorld(void *zone,void *console,const char *path) {
    return load_world(zone,console,path,false,true);
}
__declspec(dllexport) int RepopulatedLoadWorldVisual(void *zone,void *console,const char *path) {
    return load_world(zone,console,path,true,true);
}

/* The native level handler appends clusters; unlike a full load, this retains
 * all existing watched references. Called only for validated additions. */
__declspec(dllexport) int RepopulatedAppendWorldVisual(void *zone,void *console,const char *path) {
    return load_world(zone,console,path,true,false);
}

static int damage_fixture(void *zone,int faction,bool destroy) {
    initialize();
    const char *name="?removeHealth@Block@@QEAAMMPEAU1@H@Z";
    const unsigned char prefix[]={0x48,0x89,0x5c,0x24,0x18};
    HMODULE game=GetModuleHandleW(NULL);
    if (!compatible || !zone || !signature(game,name,prefix,sizeof(prefix))) return -1;
    uintptr_t vector[2];
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        vector[1]-vector[0]>4096*8) return -2;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,command,blocks[2]; int current;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x118),&current,4) || current!=faction ||
            !read_mem((void*)(cluster+0x108),&command,8) || !command ||
            !read_mem((void*)(cluster+0xf0),blocks,sizeof(blocks)) || blocks[1]<=blocks[0] ||
            blocks[1]-blocks[0]>4096*8) continue;
        for(size_t j=0;j<(blocks[1]-blocks[0])/8;j++) {
            uintptr_t block; unsigned type; float health;
            if (!read_mem((void*)(blocks[0]+j*8),&block,8) || block==command ||
                !read_mem((void*)(block+0x18),&type,4) || type!=803 ||
                !read_mem((void*)(block+0x4c),&health,4) || !isfinite(health) || health<=0 || health>10000) continue;
            typedef float (*Damage)(void*,float,void*,int); Damage damage;
            FARPROC raw=GetProcAddress(game,name); memcpy(&damage,&raw,sizeof(raw));
            float applied=damage((void*)block,destroy ? health+1.0f : health*0.25f,NULL,0);
            float remaining;
            if (!destroy && (!read_mem((void*)(block+0x4c),&remaining,4) ||
                !isfinite(remaining) || remaining<=0 || remaining>=health)) return -3;
            return applied>0 ? 1 : 0;
        }
    }
    return 0;
}

__declspec(dllexport) int RepopulatedDamageFixture(void *zone,int faction) {
    return damage_fixture(zone,faction,true);
}

__declspec(dllexport) int RepopulatedPartialDamageFixture(void *zone,int faction) {
    return damage_fixture(zone,faction,false);
}

/* Read actual runtime health, rather than trusting serializer round trips. */
__declspec(dllexport) int RepopulatedReadHealth(void *zone,unsigned ident,float *output,int capacity) {
    initialize();
    HMODULE game=GetModuleHandleW(NULL);
    const unsigned char damage_sig[]={0x48,0x89,0x5c,0x24,0x18};
    if (!compatible || !zone || !ident || !output || capacity<1 || capacity>4096 ||
        !signature(game,"?removeHealth@Block@@QEAAMMPEAU1@H@Z",damage_sig,sizeof(damage_sig))) return -1;
    uintptr_t vector[2],match=0;
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8) return -2;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x178),&parent,8)) return -3;
        if (parent || RepopulatedClusterIdent((void*)cluster)!=ident) continue;
        if (match) return -4;
        match=cluster;
    }
    if (!match) return -5;
    uintptr_t blocks[2];
    if (!read_mem((void*)(match+0xf0),blocks,sizeof(blocks)) || blocks[1]<blocks[0] ||
        (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>(uintptr_t)capacity*8) return -6;
    size_t count=(blocks[1]-blocks[0])/8;
    for(size_t j=0;j<count;j++) {
        uintptr_t block; float health;
        if (!read_mem((void*)(blocks[0]+j*8),&block,8) ||
            !read_mem((void*)(block+0x4c),&health,4) || !isfinite(health) || health<0 || health>1e9f) return -7;
        output[j]=health;
    }
    return (int)count;
}
