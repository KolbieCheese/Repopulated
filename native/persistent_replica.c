/* Exact x64 build, simulation-thread only. Removal follows native Clear's
 * remove/kill/deferred-free sequence without clearing unrelated objects. */
struct ReplicaPose { unsigned ident,faction;float x,y,vx,vy,angle,energy,resources,capacity; };
#include "replica_runtime_health.h"
_Static_assert(sizeof(struct ReplicaPose)==40,"replica pose layout");
static int replica_runtime_bulk_mode(void *zone);
static void replica_runtime_write(void *destination,const void *source,size_t size){memcpy(destination,source,size);}
static unsigned long long replica_retained,replica_removed,replica_health_applied;
static bool replica_native_player;
static uintptr_t replica_render_pilot;
static unsigned long long replica_render_calls;
typedef void (*ReplicaBlockRender)(void*,void*);
static ReplicaBlockRender replica_block_render;
__declspec(dllexport) void RepopulatedSetBlockRenderOriginal(void *original){memcpy(&replica_block_render,&original,8);}
__declspec(dllexport) void RepopulatedObserveBlockRender(void *block,void *meshes){
    /* This exact native entry runs once per visible block. A JS interceptor
     * here distorts timing measurements in a busy galaxy. */
    uintptr_t cluster;memcpy(&cluster,(char*)block+0xb8,8);
    if(cluster==replica_render_pilot)replica_render_calls++;
    if(replica_block_render)replica_block_render(block,meshes);
}
__declspec(dllexport) unsigned long long RepopulatedBlockRenderCalls(void){return replica_render_calls;}
static uintptr_t replica_find(void *zone,unsigned ident) {
    uintptr_t roots[2],match=0;
    if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || (roots[1]-roots[0])%8 || roots[1]-roots[0]>4096*8)return 0;
    for(size_t i=0;i<(roots[1]-roots[0])/8;i++) {
        uintptr_t cluster,parent;
        if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8))return 0;
        if(parent || RepopulatedClusterIdent((void*)cluster)!=ident)continue;
        if(match)return 0;
        match=cluster;
    }
    return match;
}
#include "replica_ownership.h"
struct ReplicaRootRef {unsigned ident;int faction,native_faction;uintptr_t cluster,command;};
static char replica_root_index_message[384];
static int replica_root_compare(const void *a,const void *b){
    unsigned x=((const struct ReplicaRootRef*)a)->ident,y=((const struct ReplicaRootRef*)b)->ident;
    return (x>y)-(x<y);
}
static int replica_root_index(void *zone,struct ReplicaRootRef *out,int *actual_roots){
    uintptr_t clusters[4096];size_t length;
    bool bulk=replica_runtime_bulk_mode(zone)==1;
    if(!replica_roots_copy(zone,clusters,&length)){snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Root vector unreadable or malformed, zone=%p",zone);return -1;}
    int count=0;if(actual_roots)*actual_roots=0;
    for(size_t i=0;i<length;i++){
        uintptr_t cluster=clusters[i],parent,command,serial=0;int faction,native_faction;unsigned ident=0;
        unsigned char header[0x180];uintptr_t owner=0;
        bool readable=bulk?read_mem((void*)cluster,header,sizeof(header)):
            read_mem((void*)(cluster+0x108),header+0x108,0x78);
        if(!readable){snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Root header unreadable, slot=%zu cluster=%p",i,(void*)cluster);return -1;}
        if(bulk){memcpy(&owner,header+8,8);if(owner!=(uintptr_t)zone){snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Root owner changed, slot=%zu cluster=%p",i,(void*)cluster);return -1;}}
        memcpy(&parent,header+0x178,8);if(parent)continue;
        if(actual_roots)(*actual_roots)++;
        memcpy(&command,header+0x108,8);memcpy(&native_faction,header+0x118,4);faction=replica_commandless_owner();
        if(command){
            unsigned char block[0xc0];uintptr_t owner;uint64_t features;
            if(read_mem((void*)command,block,sizeof(block))){
                memcpy(&owner,block+0xb8,8);memcpy(&features,block+0x40,8);
                if(owner==cluster && (features&1)){
                    memcpy(&serial,block+0x28,8);
                    if(serial && (!read_mem((void*)(serial+8),&ident,4) || (ident && !replica_serial_owner(serial,ident,&faction)))){snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Owned command identity/faction unreadable, slot=%zu cluster=%p command=%p",i,(void*)cluster,(void*)command);return -1;}
                }
            }
        }
        /* Debris can retain another root's cached command; use the existing
         * stable minimum-block identity rule for these commandless roots. */
        if(!serial){
            if(bulk){
                if(!replica_runtime_minimum_ident(cluster,&ident,read_mem)){
                    snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Commandless root block vector/ownership unreadable, slot=%zu cluster=%p",i,(void*)cluster);return -1;
                }
            }else ident=RepopulatedClusterIdent((void*)cluster);
        }
        if(ident && native_faction!=faction)InterlockedIncrement64(&replica_ownership_counts[serial?3:1]);
        if(ident)out[count++]=(struct ReplicaRootRef){ident,faction,native_faction,cluster,serial?command:0};
    }
    qsort(out,count,sizeof(*out),replica_root_compare);
    for(int i=1;i<count;i++)if(out[i-1].ident==out[i].ident){
        snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Duplicate root identity=0x%08x pointers=%p/%p factions=%d/%d roots=%d",out[i].ident,(void*)out[i-1].cluster,(void*)out[i].cluster,out[i-1].faction,out[i].faction,count);return -2;
    }
    snprintf(replica_root_index_message,sizeof(replica_root_index_message),"Indexed %d identified roots from %zu clusters",count,length);
    return count;
}
static const struct ReplicaRootRef *replica_root_lookup(const struct ReplicaRootRef *rows,int count,unsigned ident){
    int lo=0,hi=count;
    while(lo<hi){int mid=lo+(hi-lo)/2;if(rows[mid].ident<ident)lo=mid+1;else hi=mid;}
    return lo<count && rows[lo].ident==ident?rows+lo:NULL;
}
__declspec(dllexport) int RepopulatedCountReplicaRoots(void *zone){
    initialize();if(!compatible || !zone)return -1;
    /* This verification scans native identity only, not our motion cache.
     * Holding that cache across the scan can make both camera/draw skip and
     * expose vanilla's ahead-of-buffer render history. The private Console
     * configuration pins the caller's zone/update thread, never native objects. */
    if(atomic_load_explicit(&replica_field_context.console,memory_order_acquire) &&
       (replica_field_context.zone!=(uintptr_t)zone || replica_field_context.thread!=GetCurrentThreadId()))return -1;
    struct ReplicaRootRef index[4096];int actual=0,count=replica_root_index(zone,index,&actual);
    int result=count;
    if(count<0 || count!=actual)result=-2;
    else {
        const struct ReplicaRootRef *pilot=replica_root_lookup(index,count,0x70000002),*host=replica_root_lookup(index,count,0x70000001);
        if(!pilot || pilot->faction!=20008 || (host && host->faction!=100))result=-3;
    }
    return result;
}
__declspec(dllexport) void RepopulatedObservePilot(void *zone,unsigned ident){replica_render_pilot=replica_find(zone,ident);}
struct ReplicaMotion { unsigned ident;float angular; };
_Static_assert(sizeof(struct ReplicaMotion)==8,"replica motion layout");
__declspec(dllexport) int RepopulatedApplyReplicaMotion(void *zone,const struct ReplicaMotion *motion,int count) {
    initialize();if(!compatible || !zone || count<0 || count>4096 || (count && !motion))return -1;
    HMODULE game=GetModuleHandleW(NULL);const char *name="?setAngVel@Body@@QEAAXM@Z";
    const unsigned char prefix[]={0xc5,0xfa,0x11,0x49,0x64,0xc3};
    if(!signature(game,name,prefix,sizeof(prefix)))return -2;
    typedef void (*Set)(void*,float);Set set;FARPROC raw=GetProcAddress(game,name);memcpy(&set,&raw,8);
    for(int i=0;i<count;i++){
        uintptr_t cluster=replica_find(zone,motion[i].ident);
        if(!cluster || !isfinite(motion[i].angular) || fabsf(motion[i].angular)>10000)return -3;
        set((void*)cluster,motion[i].angular);
    }
    return count;
}
typedef int (*ReplicaNativeRemove)(void*,int);
typedef void (*ReplicaNativeKill)(void*,int);
typedef void (*ReplicaNativeFree)(void*);
struct ReplicaRemoveFunctions {ReplicaNativeRemove remove;ReplicaNativeKill kill;ReplicaNativeFree release;};
static void replica_remove_trace(unsigned ident,uintptr_t cluster){
    for(int i=0;i<2;i++)if(ident==0x70000001u+(unsigned)i || (cluster && replica_motion_trace_pointer[i]==cluster)){
        replica_motion_trace_pointer[i]=0;replica_motion_trace_observed[i]=0;memset(replica_motion_trace[i],0,sizeof(replica_motion_trace[i]));
    }
}
static void replica_remove_missing(unsigned ident,uintptr_t cluster){
    replica_remove_trace(ident,cluster);
    /* Native housekeeping can already remove a terminal replica (especially
     * an expired missile) before the next authoritative membership update. */
    struct ReplicaSmoothing *history=replica_history(ident,false);
    if(history){
        if(history->motion_cluster && history->motion_cluster==replica_render_pilot){replica_render_pilot=0;prediction_reset();}
        InterlockedIncrement64(&replica_replacement_counts[7]);
        InterlockedExchange(&replica_emitter_publications[history-replica_smoothing].key,0);memset(history,0,sizeof(*history));
    }
    if(cluster && cluster==replica_render_pilot){replica_render_pilot=0;prediction_reset();}
}
static int replica_remove_resolved(void *zone,unsigned ident,unsigned command_block,int faction,unsigned kind,
                                   uintptr_t cluster,const struct ReplicaRemoveFunctions *functions){
    replica_remove_trace(ident,cluster);
    struct ReplicaSmoothing *history=replica_history(ident,false);
    /* Invalidate every old native pointer before deferred native release.
     * A verified shape replacement retains only source samples and its
     * stable command/minimum-block identity; removals discard everything. */
    if(history){
        InterlockedExchange(&replica_emitter_publications[history-replica_smoothing].key,0);
        if(kind){
            bool retained=replica_replacement_detach_kind(history,zone,cluster,command_block,faction,kind,read_mem);
            InterlockedIncrement64(&replica_replacement_counts[retained?(kind==REPLICA_REPLACEMENT_COMMAND?0:1):(kind==REPLICA_REPLACEMENT_COMMAND?4:5)]);
        }else{InterlockedIncrement64(&replica_replacement_counts[7]);memset(history,0,sizeof(*history));}
    }else if(kind){
        InterlockedIncrement64(&replica_replacement_counts[kind==REPLICA_REPLACEMENT_COMMAND?4:5]);
    }
    if(cluster==replica_render_pilot){replica_render_pilot=0;prediction_reset();}
    functions->remove((void*)cluster,-1);functions->kill((void*)cluster,-1);functions->release((void*)cluster);
    replica_removed++;return 1;
}
static int replica_remove(void *zone,unsigned ident,unsigned command_block,int faction,unsigned kind) {
    initialize();HMODULE game=GetModuleHandleW(NULL);
    const char *remove_name="?removeFromGameZone@BlockCluster@@QEAAHH@Z";
    const char *free_name="?pool_free_mainthread@BlockCluster@@SAXPEAU1@@Z";
    const char *kill_name="?killRecursive@BlockCluster@@QEAAXH@Z";
    const unsigned char remove_sig[]={0x48,0x89,0x5c,0x24,0x18,0x56};
    const unsigned char free_sig[]={0x48,0x85,0xc9,0x74,0x75,0x53};
    if(!compatible || !zone || !ident || !signature(game,remove_name,remove_sig,sizeof(remove_sig)) ||
       !signature(game,free_name,free_sig,sizeof(free_sig)))return -1;
    uintptr_t cluster=replica_find(zone,ident);
    if(!cluster){replica_remove_missing(ident,0);return 0;}
    struct ReplicaRemoveFunctions functions;FARPROC raw=GetProcAddress(game,remove_name);memcpy(&functions.remove,&raw,8);
    raw=GetProcAddress(game,kill_name);if(!raw)return -1;memcpy(&functions.kill,&raw,8);
    raw=GetProcAddress(game,free_name);memcpy(&functions.release,&raw,8);
    return replica_remove_resolved(zone,ident,command_block,faction,kind,cluster,&functions);
}
__declspec(dllexport) int RepopulatedRemoveReplica(void *zone,unsigned ident){
    initialize();if(!compatible || !zone || !replica_guard_enter(zone))return -2;
    int result=replica_remove(zone,ident,0,-1,0);replica_guard_leave(zone);return result;
}
__declspec(dllexport) int RepopulatedRemoveReplicaForReplacement(void *zone,unsigned ident,unsigned command_block,int faction){
    initialize();if(!compatible || !zone || !replica_guard_enter(zone))return -2;
    int result=replica_remove(zone,ident,command_block,faction,REPLICA_REPLACEMENT_COMMAND);replica_guard_leave(zone);return result;
}
__declspec(dllexport) int RepopulatedRemoveReplicaForFragmentReplacement(void *zone,unsigned ident,unsigned anchor){
    initialize();if(!compatible || !zone || !ident || anchor!=ident || !replica_guard_enter(zone))return -2;
    int result=replica_remove(zone,ident,anchor,0,REPLICA_REPLACEMENT_FRAGMENT);replica_guard_leave(zone);return result;
}
static int replica_update(void *zone,const struct ReplicaPose *poses,int count,
                                                const struct ReplicaHealth *health,int blocks) {
    initialize();HMODULE game=GetModuleHandleW(NULL);int mode=replica_runtime_bulk_mode(zone);if(mode<0)return -5;bool bulk=mode==1;
    const char *pos_name="?setPos@BlockCluster@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
    const char *vel_name="?setVel@Body@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
    const char *angle_name="?setAngle@BlockCluster@@QEAAXM@Z";
    const char *max_name="?getMaxHealth@Block@@QEBAMXZ";
    const unsigned char pos_sig[]={0x48,0x89,0x5c,0x24,0x18};
    const unsigned char vel_sig[]={0x48,0x89,0x54,0x24,0x08};
    const unsigned char angle_sig[]={0x40,0x53,0x48,0x83,0xec,0x30};
    const unsigned char max_sig[]={0xc5,0xfa,0x10,0x81,0xf8,0,0,0};
    if(!compatible || !zone || !poses || !health || count<1 || count>4096 || blocks<1 || blocks>65536 ||
       !signature(game,pos_name,pos_sig,sizeof(pos_sig)) || !signature(game,vel_name,vel_sig,sizeof(vel_sig)) ||
       !signature(game,angle_name,angle_sig,sizeof(angle_sig)) || !signature(game,max_name,max_sig,sizeof(max_sig)))return -1;
    typedef void (*Setter)(void*,uint64_t);typedef void (*Angle)(void*,float);typedef float (*Max)(void*);
    Setter position,velocity;Angle rotation;Max maximum;FARPROC raw=GetProcAddress(game,pos_name);memcpy(&position,&raw,8);
    raw=GetProcAddress(game,vel_name);memcpy(&velocity,&raw,8);raw=GetProcAddress(game,angle_name);memcpy(&rotation,&raw,8);
    raw=GetProcAddress(game,max_name);memcpy(&maximum,&raw,8);
    if(!replica_runtime_health_valid(health,blocks))return -4;
    struct ReplicaRootRef index[4096];int roots_count=replica_root_index(zone,index,NULL);if(roots_count<0)return -2;
    for(int i=0;i<count;i++) {
        const struct ReplicaPose *p=&poses[i];const struct ReplicaRootRef *ref=replica_root_lookup(index,roots_count,p->ident);uintptr_t cluster=ref?ref->cluster:0;
        if(!cluster || !isfinite(p->x) || !isfinite(p->y) || !isfinite(p->vx) || !isfinite(p->vy) || !isfinite(p->angle) ||
           fabsf(p->x)>1e6f || fabsf(p->y)>1e6f || fabsf(p->vx)>10000 || fabsf(p->vy)>10000)return -2;
        if(bulk){struct ReplicaRuntimeCluster current;if(!replica_runtime_cluster_read(cluster,(uintptr_t)zone,&current,read_mem) || current.parent)return -2;}
        union {float xy[2];uint64_t packed;} pos={.xy={p->x+world_visual_center[0],p->y+world_visual_center[1]}},vel={.xy={p->vx,p->vy}};
        /* Structural snapshots are intentionally slower and can be older than
         * the retained root's fast frame. They must not rewind native camera
         * inputs or restart its presentation correction. */
        if(!replica_guard_enter(zone))return -5;
        if(!replica_has_fast_motion(zone,p->ident,cluster)){
            replica_correction(p->ident,pos.xy[0],pos.xy[1],p->angle);
            rotation((void*)cluster,p->angle);position((void*)cluster,pos.packed);velocity((void*)cluster,vel.packed);
        }
        replica_guard_leave(zone);
        /* These owned-update-thread writes neither change the presentation
         * cache nor remove native objects. Do not hold its guard through the
         * full retained block-health traversal and stall camera/draw updates. */
        uintptr_t command=owned_cluster_command((void*)cluster),serial;
        if(command && p->faction){
            if(!read_mem((void*)(command+0x28),&serial,8) || !serial || !isfinite(p->energy) || p->energy<0 || p->energy>1e9f ||
               !isfinite(p->resources) || p->resources<0 || p->resources>1e9f || !isfinite(p->capacity) || p->capacity<0 || p->capacity>1e9f)return -3;
            int energy=(int)p->energy;memcpy((void*)(serial+0x1c),&energy,4);
            memcpy((void*)(serial+0x14),&p->resources,4);memcpy((void*)(serial+0x18),&p->capacity,4);
            uintptr_t metadata;if(read_mem((void*)(cluster+0x110),&metadata,8) && metadata)memcpy((void*)(metadata+0x1c),&p->energy,4);
        }
        if(bulk){
            int applied=replica_runtime_health_apply(cluster,health,blocks,true,read_mem,replica_runtime_write,maximum,&replica_health_applied);
            if(applied<0)return applied;
            replica_retained++;continue;
        }
        uintptr_t vector[2];if(!read_mem((void*)(cluster+0xf0),vector,16) || vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8)return -3;
        for(size_t j=0;j<(vector[1]-vector[0])/8;j++) {
            uintptr_t block;unsigned ident;if(!read_mem((void*)(vector[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&ident,4))return -3;
            const struct ReplicaHealth *state=NULL;int lo=0,hi=blocks;
            while(lo<hi){int mid=lo+(hi-lo)/2;if(health[mid].ident<ident)lo=mid+1;else hi=mid;}
            if(lo<blocks && health[lo].ident==ident)state=&health[lo];
            if(!state || !isfinite(state->health) || (state->health!=-1 && (state->health<0 || state->health>1e9f)))return -4;
            float value=state->health<0?maximum((void*)block):state->health;
            memcpy((void*)(block+0x4c),&value,4);replica_health_applied++;
            if(state->growth>=0)memcpy((void*)(block+0x48),&state->growth,4);
            if(state->lifetime>=0)memcpy((void*)(block+0x38),&state->lifetime,4);
        }
        replica_retained++;
    }
    /* GameZone's vector also owns attached subclusters. Their timers and
     * health change without changing parent geometry. Update matching block
     * identities; native cleanup may already have removed an expired child. */
    if(bulk){
        uintptr_t children[4096];size_t length;if(!replica_roots_copy(zone,children,&length))return -3;
        for(size_t i=0;i<length;i++){
            struct ReplicaRuntimeCluster current;
            if(!replica_runtime_cluster_read(children[i],(uintptr_t)zone,&current,read_mem))return -3;
            if(!current.parent)continue;
            int applied=replica_runtime_health_apply(children[i],health,blocks,false,read_mem,replica_runtime_write,maximum,&replica_health_applied);
            if(applied<0)return applied;
        }
        return count;
    }
    uintptr_t roots[2];
    if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || (roots[1]-roots[0])%8 || roots[1]-roots[0]>4096*8)return -3;
    for(size_t i=0;i<(roots[1]-roots[0])/8;i++){
        uintptr_t cluster,parent,vector[2];
        if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8))return -3;
        if(!parent)continue;
        if(!read_mem((void*)(cluster+0xf0),vector,16) || vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8)return -3;
        for(size_t j=0;j<(vector[1]-vector[0])/8;j++){
            uintptr_t block;unsigned ident;
            if(!read_mem((void*)(vector[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&ident,4))return -3;
            int lo=0,hi=blocks;while(lo<hi){int mid=lo+(hi-lo)/2;if(health[mid].ident<ident)lo=mid+1;else hi=mid;}
            if(lo==blocks || health[lo].ident!=ident)continue;
            const struct ReplicaHealth *state=&health[lo];float value=state->health<0?maximum((void*)block):state->health;
            memcpy((void*)(block+0x4c),&value,4);replica_health_applied++;
            if(state->growth>=0)memcpy((void*)(block+0x48),&state->growth,4);
            if(state->lifetime>=0)memcpy((void*)(block+0x38),&state->lifetime,4);
        }
    }
    return count;
}
__declspec(dllexport) int RepopulatedUpdateReplica(void *zone,const struct ReplicaPose *poses,int count,const struct ReplicaHealth *health,int blocks){
    initialize();if(!compatible || !zone)return -5;
    return replica_update(zone,poses,count,health,blocks);
}
#include "scene_gate.h"
static int replica_runtime_bulk_mode(void *zone){
    uintptr_t console=atomic_load_explicit(&replica_field_context.console,memory_order_acquire),owner;
    if(!console)return 0; /* Unconfigured research/sandbox keeps its legacy path. */
    DWORD thread=GetCurrentThreadId();
    if(replica_field_context.zone!=(uintptr_t)zone || replica_field_context.thread!=thread ||
       !read_mem((void*)(console+8),&owner,8) || owner!=(uintptr_t)zone)return -1;
    LONG state=InterlockedCompareExchange(&replica_scene_gate.state,0,0);
    if(!state)return 0; /* Initial private import precedes gate configuration. */
    return replica_scene_gate.zone==zone && replica_scene_gate.owner==thread && state==REPLICA_SCENE_MUTATING?1:-1;
}
#define REPLICA_SCENE_PACING_BEGIN() replica_scene_idle_begin()
#define REPLICA_SCENE_PACING_END() ((void)0)
#include "frame_pacing.h"
/* The source naturally runs after swap. Keep the completed-frame idle phase
 * through swap/deferred work, and close it before Render enters any engine
 * lock or updates body render samples. Exact win64 caller169d70 releases its
 * deferred-queue SRW before this entry at118730; the entry takes state SRW. */
typedef void (*ReplicaRenderBoundaryOriginal)(void*);
static ReplicaRenderBoundaryOriginal replica_render_boundary_original;
__declspec(dllexport) void RepopulatedSetRenderBoundaryOriginal(void *original){memcpy(&replica_render_boundary_original,&original,sizeof(original));}
__declspec(dllexport) void RepopulatedRenderBoundary(void *state){
    replica_scene_idle_end();
    if(replica_render_boundary_original)replica_render_boundary_original(state);
}
__declspec(dllexport) int RepopulatedConfigureSceneGate(void *zone){
    initialize();return compatible?replica_scene_configure(zone):-1;
}
__declspec(dllexport) int RepopulatedTryBeginSceneUpdate(void *zone){return replica_scene_try_begin(zone);}
__declspec(dllexport) int RepopulatedRequestSceneHandoff(void *zone,int pending){return replica_scene_request(zone,pending);}
__declspec(dllexport) int RepopulatedEndSceneUpdate(void *zone,int committed){return replica_scene_end(zone,committed);}
__declspec(dllexport) void RepopulatedSceneGateStats(double *out){replica_scene_stats(out);}
__declspec(dllexport) void RepopulatedSceneHandoffStats(double *out){replica_scene_handoff_stats(out);}
#include "replica_remove_batch.h"
static char replica_remove_batch_message[512];
__declspec(dllexport) const char *RepopulatedRemoveReplicaBatchMessage(void){return replica_remove_batch_message;}
struct ReplicaRemoveBatchContext {void *zone;struct ReplicaRemoveFunctions functions;};
static FARPROC replica_remove_batch_function(HMODULE game,const char *name,const unsigned char *prefix,size_t length){
    FARPROC original=GetProcAddress(game,name);unsigned char actual[16];
    return original && length<=sizeof(actual) && read_mem((void*)original,actual,length) &&
        !memcmp(actual,prefix,length)?original:NULL;
}
static int replica_remove_batch_index(void *context,struct ReplicaRemoveCandidate *out,int capacity){
    struct ReplicaRemoveBatchContext *batch=context;
    struct ReplicaRootRef roots[REPLICA_REMOVE_BATCH_LIMIT];
    int count=replica_root_index(batch->zone,roots,NULL);
    if(count<0 || count>capacity)return -1;
    for(int i=0;i<count;i++)out[i]=(struct ReplicaRemoveCandidate){roots[i].ident,roots[i].cluster};
    return count;
}
static bool replica_remove_batch_live(void *context,uintptr_t *roots,size_t *length){
    return replica_roots_copy(((struct ReplicaRemoveBatchContext*)context)->zone,roots,length);
}
static bool replica_remove_batch_identity(void *context,uintptr_t root,unsigned ident){
    void *zone=((struct ReplicaRemoveBatchContext*)context)->zone;
    /* The live membership copy must precede these reads. A pool address can
     * remain readable after release; read permission is not a lifetime test. */
    for(int check=0;check<2;check++){
        uintptr_t owner,parent;
        if(!read_mem((void*)(root+8),&owner,8) || owner!=(uintptr_t)zone ||
           !read_mem((void*)(root+0x178),&parent,8) || parent ||
           RepopulatedClusterIdent((void*)root)!=ident)return false;
    }
    return true;
}
static void replica_remove_batch_missing(void *context,unsigned ident,uintptr_t root){
    (void)context;replica_remove_missing(ident,root);
}
static int replica_remove_batch_one(void *context,const struct ReplicaRemoveDescriptor *row,uintptr_t root){
    struct ReplicaRemoveBatchContext *batch=context;
    return replica_remove_resolved(batch->zone,row->ident,row->replacement?row->command_block:0,
        row->replacement?row->faction:-1,row->replacement,root,&batch->functions);
}
__declspec(dllexport) int RepopulatedRemoveReplicaBatch(void *zone,const struct ReplicaRemoveDescriptor *rows,int count){
    initialize();
    if(!compatible || !zone || count<0 || count>REPLICA_REMOVE_BATCH_LIMIT || (count && !rows)){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Invalid batch/zone, count=%d",count);return -1;
    }
    struct ReplicaRemoveDescriptor staged[REPLICA_REMOVE_BATCH_LIMIT];int bad_row=-1;
    if(count && !read_mem(rows,staged,(size_t)count*sizeof(*rows))){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Unreadable descriptors, count=%d",count);return -1;
    }
    int valid=replica_remove_descriptors_valid(staged,count,&bad_row);
    if(valid<0){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Invalid descriptor row=%d count=%d",bad_row,count);return valid;
    }
    uintptr_t console=atomic_load_explicit(&replica_field_context.console,memory_order_acquire),owner=0;
    DWORD thread=GetCurrentThreadId();
    bool console_valid=console && read_mem((void*)(console+8),&owner,8);
    struct ReplicaRemoveAuthority authority={(uintptr_t)zone,owner,replica_field_context.zone,
        (uintptr_t)replica_scene_gate.zone,thread,replica_field_context.thread,replica_scene_gate.owner,console_valid,
        InterlockedCompareExchange(&replica_scene_gate.state,0,0)==REPLICA_SCENE_MUTATING};
    if(!replica_remove_batch_authorized(authority)){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Batch outside configured private update transaction, zone=%p thread=%lu",zone,thread);return -7;
    }
    if(!count){snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Removed 0/0 roots");return 0;}
    HMODULE game=GetModuleHandleW(NULL);
    const char *remove_name="?removeFromGameZone@BlockCluster@@QEAAHH@Z";
    const char *kill_name="?killRecursive@BlockCluster@@QEAAXH@Z";
    const char *free_name="?pool_free_mainthread@BlockCluster@@SAXPEAU1@@Z";
    const unsigned char remove_sig[]={0x48,0x89,0x5c,0x24,0x18,0x56};
    const unsigned char kill_sig[]={0x48,0x89,0x5c,0x24,0x10};
    const unsigned char free_sig[]={0x48,0x85,0xc9,0x74,0x75,0x53};
    FARPROC remove_raw=replica_remove_batch_function(game,remove_name,remove_sig,sizeof(remove_sig));
    FARPROC kill_raw=replica_remove_batch_function(game,kill_name,kill_sig,sizeof(kill_sig));
    FARPROC free_raw=replica_remove_batch_function(game,free_name,free_sig,sizeof(free_sig));
    if(!remove_raw || !kill_raw || !free_raw){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Native remove/kill/deferred-release signature mismatch");return -6;
    }
    struct ReplicaRemoveBatchContext context={.zone=zone};
    memcpy(&context.functions.remove,&remove_raw,8);memcpy(&context.functions.kill,&kill_raw,8);
    memcpy(&context.functions.release,&free_raw,8);
    if(!replica_guard_enter(zone)){
        snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Presentation cache unavailable");return -7;
    }
    struct ReplicaRemoveBatchOps ops={&context,replica_remove_batch_index,replica_remove_batch_live,
        replica_remove_batch_identity,replica_remove_batch_one,replica_remove_batch_missing};
    struct ReplicaRemoveFailure failure;
    int result=replica_remove_batch_run(staged,count,&ops,&failure);
    replica_guard_leave(zone);
    if(result<0)snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),
        "Batch failed stage=%d row=%d identity=0x%08x candidate=%p alreadyRemoved=%d index=%s",
        failure.stage,failure.row,failure.ident,(void*)failure.root,failure.removed,replica_root_index_message);
    else snprintf(replica_remove_batch_message,sizeof(replica_remove_batch_message),"Removed %d/%d roots",result,count);
    return result;
}
__declspec(dllexport) void RepopulatedReplicaStats(unsigned long long *values) {
    if(values){values[0]=replica_retained;values[1]=replica_removed;values[2]=replica_health_applied;}
}
__declspec(dllexport) int RepopulatedBindReplicaPlayer(void *zone,unsigned ident) {
    initialize();if(!compatible || !zone || ident!=0x70000002)return -1;
    HMODULE game=GetModuleHandleW(NULL);uintptr_t player,command;
    uintptr_t cluster=replica_find(zone,ident);
    if(!cluster || !read_mem((void*)(cluster+0x108),&command,8) || !command ||
       !read_mem((char*)game+0x3cf930,&player,8) || !player)return -2;
    const unsigned char prefix[]={0x48,0x8b,0xc4,0x53,0x55,0x56,0x57,0x41,0x55};
    uintptr_t address=(uintptr_t)game+0x1dd170;unsigned char actual[sizeof(prefix)];
    if(!read_mem((void*)address,actual,sizeof(actual)) || memcmp(actual,prefix,sizeof(prefix)))return -3;
    typedef void (*Bind)(void*,void*);Bind bind;memcpy(&bind,&address,8);
    bind((void*)player,(void*)command);
    replica_native_player=true;
    if(replica_render_pilot!=cluster)prediction_reset();replica_render_pilot=cluster;
    uintptr_t current;return read_mem((void*)(player+0xa8),&current,8) && current==command?1:-4;
}
static void replica_player_update(void *ai) {
    if(!replica_native_player || !compatible)return;
    HMODULE game=GetModuleHandleW(NULL);uintptr_t player,command,controlled;
    if(!read_mem((char*)game+0x3cf930,&player,8) || !player || !read_mem((void*)(player+0xa8),&controlled,8) || !controlled ||
       !read_mem((char*)ai+0x278,&command,8) || command!=controlled)return;
    typedef bool (*Update)(void*);Update update;FARPROC raw=GetProcAddress(game,"?playerUpdate@AI@@AEAA_NXZ");
    memcpy(&update,&raw,8);if(update)update(ai);
}
__declspec(dllexport) int RepopulatedDriveNative(void *zone,int owner,unsigned ident,unsigned dimensions,
                                               const float *destination,const float *precision,bool stale) {
    initialize();if(!compatible || !zone || owner!=20008 || ident!=0x70000002 || !destination || !precision || dimensions&~0x1df)return -1;
    uintptr_t cluster=replica_find(zone,ident),command;int faction;
    if(!cluster || !read_mem((void*)(cluster+0x118),&faction,4) || faction!=owner ||
       !read_mem((void*)(cluster+0x108),&command,8) || !command)return 0;
    HMODULE game=GetModuleHandleW(NULL);typedef void* (*GetAI)(void*);typedef void (*Config)(void*,void*);typedef bool (*Update)(void*);
    GetAI get_ai;Config config;Update update;FARPROC raw=GetProcAddress(game,"?getCommandAI@Block@@QEAAPEAUAI@@XZ");memcpy(&get_ai,&raw,8);
    raw=GetProcAddress(game,"?getNavConfig@BlockCluster@@QEBAXPEAUsnConfig@@@Z");memcpy(&config,&raw,8);
    uintptr_t address=(uintptr_t)game+0x43ef0;memcpy(&update,&address,8);
    if(!get_ai || !config)return -2;
    for(int i=0;i<6;i++)if(!isfinite(destination[i]) || fabsf(destination[i])>(i<2?1e6f:i==4?100:10000))return -3;
    for(int i=0;i<4;i++)if(!isfinite(precision[i]) || precision[i]<0 || precision[i]>(i<2?10000:100))return -3;
    void *ai=get_ai((void*)command);if(!ai)return -4;
    float desired[6];memcpy(desired,destination,sizeof(desired));
    if(stale){memset(desired,0,sizeof(desired));dimensions=0x10a;}
    double position[2];if(!read_mem((void*)(cluster+0x30),position,16))return -4;
    desired[0]+=(float)position[0];desired[1]+=(float)position[1];
    memcpy((char*)ai+0x2a8,precision,16);memcpy((char*)ai+0x2b8,&dimensions,4);memcpy((char*)ai+0x2bc,desired,24);
    config((void*)cluster,(char*)ai+0x2d4);update((char*)ai+0x280);return 1;
}
struct NativeWeaponIntent { unsigned ident,mask;float x,y,vx,vy,spread,aim; };
__declspec(dllexport) int RepopulatedBrakeHostInMenu(void *ai,void *zone){
    initialize();if(!compatible || !ai || !zone)return -1;
    uintptr_t command,cluster,actual_zone;int faction;
    if(!read_mem((char*)ai+0x228,&actual_zone,8) || actual_zone!=(uintptr_t)zone ||
       !read_mem((char*)ai+0x278,&command,8) || !command || !read_mem((void*)(command+0xb8),&cluster,8) ||
       !cluster || RepopulatedClusterIdent((void*)cluster)!=0x70000001 || !read_mem((void*)(cluster+0x118),&faction,4) || faction!=100)return 0;
    HMODULE game=GetModuleHandleW(NULL);const char *name="?getNavConfig@BlockCluster@@QEBAXPEAUsnConfig@@@Z";
    const unsigned char sig[]={0x48,0x89,0x5c,0x24,0x10};
    const unsigned char nav_sig[]={0x40,0x53,0x41,0x56,0x48,0x81,0xec,0xa8,0,0,0};
    if(!signature(game,name,sig,sizeof(sig)) || memcmp((char*)game+0x43ef0,nav_sig,sizeof(nav_sig)))return -2;
    if(RepopulatedReleaseWeapons((void*)cluster,100,0x70000001)<0)return -3;
    double pos[2];float desired[6]={0};unsigned dimensions=0x10a;
    if(!read_mem((void*)(cluster+0x30),pos,16) || !isfinite(pos[0]) || !isfinite(pos[1]))return -4;
    desired[0]=(float)pos[0];desired[1]=(float)pos[1];
    memcpy((char*)ai+0x2b8,&dimensions,4);memcpy((char*)ai+0x2bc,desired,24);
    typedef void (*Config)(void*,void*);Config config;FARPROC raw=GetProcAddress(game,name);memcpy(&config,&raw,8);
    typedef bool (*Update)(void*);Update update=(Update)((char*)game+0x43ef0);
    config((void*)cluster,(char*)ai+0x2d4);update((char*)ai+0x280);return 1;
}
_Static_assert(sizeof(struct NativeWeaponIntent)==32,"native weapon intent layout");
__declspec(dllexport) int RepopulatedNativeWeapons(void *zone,int owner,unsigned ident,const struct NativeWeaponIntent *weapons,int count,bool stale) {
    initialize();if(!compatible || !zone || owner!=20008 || ident!=0x70000002 || count<0 || count>256 || (count && !weapons))return -1;
    uintptr_t cluster=replica_find(zone,ident),command,vector[2];int faction;double position[2];
    if(!cluster || !read_mem((void*)(cluster+0x118),&faction,4) || faction!=owner ||
       !read_mem((void*)(cluster+0x108),&command,8) || !command || !read_mem((void*)(cluster+0xf0),vector,16) ||
       vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8 || !read_mem((void*)(cluster+0x30),position,16))return -2;
    /* Expired input must release the current ship even when its weapon IDs
     * changed after damage/respawn. Never keep a stale charge request alive. */
    if(stale)return RepopulatedReleaseWeapons((void*)cluster,owner,ident)<0?-3:0;
    HMODULE game=GetModuleHandleW(NULL);typedef bool (*Set)(void*,uint64_t,bool);
    Set set;FARPROC raw=GetProcAddress(game,"?setEnabled@Block@@QEAA_N_K_N@Z");memcpy(&set,&raw,8);if(!set)return -3;
    uintptr_t selected[256];
    /* Validate the complete request against this ship before changing anything. */
    for(int i=0;i<count;i++) {
        if(!weapons[i].ident || weapons[i].mask&~0x800008e0u)return -4;
        for(int j=0;j<i;j++)if(weapons[j].ident==weapons[i].ident)return -4;
        selected[i]=0;
        for(size_t j=0;j<(vector[1]-vector[0])/8;j++) {
            uintptr_t block;unsigned bid;uint64_t features;
            if(!read_mem((void*)(vector[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&bid,4) || !read_mem((void*)(block+0x40),&features,8))return -4;
            if(bid!=weapons[i].ident)continue;
            if(selected[i] || !(features&0x8e0) || (weapons[i].mask&~(features|0x80000000ULL)))return -4;
            selected[i]=block;
        }
        if(!selected[i])return RepopulatedReleaseWeapons((void*)cluster,owner,ident)<0?-3:0;
        const float *values=&weapons[i].x;for(int j=0;j<6;j++)if(!isfinite(values[j]) || fabsf(values[j])>(j<2?1e6f:j<4?10000:100))return -4;
    }
    if(RepopulatedReleaseWeapons((void*)cluster,owner,ident)<0)return -3;
    int fired=0;
    for(int i=0;i<count;i++) {
        const struct NativeWeaponIntent *w=&weapons[i];uintptr_t turret;uint64_t features;
        if(!read_mem((void*)(selected[i]+0x40),&features,8))return -4;
        if(features&0x10){
            if(!read_mem((void*)(selected[i]+0x158),&turret,8) || !turret)return -4;
            memcpy((void*)(turret+0x10),&w->aim,4);memcpy((void*)(turret+0x14),&w->spread,4);
        }
        /* Player::doWeaponsTargetting enables manual groups directly; it
         * does not call Block::fireWeapon. Replay those native decisions.
         * Block::update on the host decides cooldown/energy and emits shots. */
        set((void*)selected[i],0x800008e0ULL,false);
        if(w->mask){set((void*)selected[i],w->mask,true);fired++;}
    }
    return fired;
}
