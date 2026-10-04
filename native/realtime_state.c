/* Exact build 8af8e056...: one simulation-thread sample for weapons, damage,
 * projectiles and thruster controls. Particles are generated locally. */
#include "mover_window.h"
static struct ReplicaMover replica_movers[4096];
static struct ReplicaMoverWindow replica_mover_window;
static int replica_mover_count;
static double replica_mover_received;
static unsigned long long replica_local_mover_updates;
static uintptr_t replica_effect_cluster;
__declspec(dllexport) void RepopulatedLocalThrust(void *system,uint64_t pos,uint64_t vel,float size,unsigned color,uint64_t vel2,float size2,unsigned color2){
    if(!original_thrust)return;
    union{uint64_t packed;float xy[2];}p={.packed=pos},v={.packed=vel},v2={.packed=vel2};
    if(replica_effect_cluster){
        uintptr_t cluster=replica_effect_cluster,parent;
        for(int depth=0;;depth++){
            if(depth>=16 || !read_mem((void*)(cluster+0x178),&parent,8)){InterlockedIncrement64(&replica_emitter_counts[4]);return;}
            if(!parent)break;cluster=parent;
        }
        unsigned ident=RepopulatedClusterIdent((void*)cluster);struct ReplicaEmitterCurve curve;struct ReplicaTimelinePose pose;
        /* Fixed publication banks let update-thread effects evaluate the same
         * source curve between display preparations. Missing metadata skips
         * one cosmetic emission; raw poses must not leak through a busy cache. */
        if(!replica_emitter_snapshot(ident,cluster,&curve)){
            InterlockedIncrement64(&replica_emitter_counts[1]);if(replica_motion_sequence)return;
        }else{
            double state[4],now=replica_now_millis();float angles[2];
            if(!replica_emitter_evaluate(&curve,now,&pose)){InterlockedIncrement64(&replica_emitter_counts[2]);return;}
            if(!read_mem((void*)(cluster+0x30),state,32) || !read_mem((void*)(cluster+0x60),angles,8) ||
               RepopulatedClusterIdent((void*)cluster)!=ident){InterlockedIncrement64(&replica_emitter_counts[4]);return;}
            replica_emitter_transform(&pose,state,angles[0],angles[1],p.xy,v.xy,v2.xy);
            InterlockedIncrement64(&replica_emitter_counts[0]);
        }
    }
    replica_thrust_audit_record(system,p.packed,v.packed,size,color,v2.packed,size2,color2);
    original_thrust(system,p.packed,v.packed,size,color,v2.packed,size2,color2);replayed_thrust++;
}
__declspec(dllexport) void RepopulatedLocalExhaustStats(double *out){
    if(out)for(int i=0;i<5;i++)out[i]=(double)InterlockedCompareExchange64(&replica_emitter_counts[i],0,0);
}
struct RealtimePacket {
    unsigned counts[4];
    struct VisualBlock blocks[4096];
    struct VisualProjectile shots[2048];
    struct ReplicaMover movers[4096];
    struct ReplicaHealth health[65536];
};
static int compare_health(const void *a,const void *b){
    unsigned x=((const struct ReplicaHealth*)a)->ident,y=((const struct ReplicaHealth*)b)->ident;
    return (x>y)-(x<y);
}
static int compare_mover(const void *a,const void *b){
    unsigned x=((const struct ReplicaMover*)a)->block_ident,y=((const struct ReplicaMover*)b)->block_ident;
    return (x>y)-(x<y);
}
static uintptr_t native_mover(uintptr_t block,uintptr_t cluster){
    uintptr_t metadata,vector[2];unsigned short index;
    if(!read_mem((void*)(block+0x178),&index,2) || index==65535 || !read_mem((void*)(cluster+0x110),&metadata,8) || !metadata || !read_mem((void*)(metadata+0x100),vector,16) || vector[1]<vector[0] || (vector[1]-vector[0])%0x34 || vector[1]-vector[0]>4096*0x34 || (size_t)index>=(vector[1]-vector[0])/0x34)return 0;
    return vector[0]+index*0x34;
}
typedef void (*ThrustAuditMoverUpdate)(void*);
static ThrustAuditMoverUpdate audit_mover_original;
__declspec(dllexport) void RepopulatedSetAuditMoverOriginal(void *address){memcpy(&audit_mover_original,&address,sizeof(address));}
__declspec(dllexport) void RepopulatedAuditMoverUpdate(void *block){
    if(!audit_mover_original)return;
    struct ReplicaThrustAuditContext previous=replica_thrust_audit_begin((uintptr_t)block);
    audit_mover_original(block);replica_thrust_audit_context=previous;
}
__declspec(dllexport) int RepopulatedReadRealtime(void *zone,unsigned focus,float radius,struct RealtimePacket *out){
    initialize();uintptr_t pilot=replica_find(zone,focus),roots[4096];size_t root_count;double center[2];
    if(!compatible || !zone || !out || !isfinite(radius) || radius<1000 || radius>20000)return -1;
    memset(out->counts,0,sizeof(out->counts));if(!pilot)return 0;
    if(!read_mem((void*)(pilot+0x30),center,16) || !replica_roots_copy(zone,roots,&root_count))return -2;
    for(size_t i=0;i<root_count;i++){
        uintptr_t cluster=roots[i],parent,root;struct ReplicaRuntimeCluster cluster_state;double pos[2];
        if(!replica_runtime_cluster_read(cluster,(uintptr_t)zone,&cluster_state,read_mem))return -2;
        parent=cluster_state.parent;
        root=cluster;
        for(int depth=0;parent;depth++){
            if(depth>=16)return -2;root=parent;
            if(!replica_runtime_cluster_read(root,(uintptr_t)zone,&cluster_state,read_mem))return -2;
            parent=cluster_state.parent;
        }
        memcpy(pos,cluster_state.position,sizeof(pos));
        if(!near_focus(pos[0],pos[1],center,radius))continue;
        unsigned ident=RepopulatedClusterIdent((void*)cluster);
        uintptr_t pointers[4096];size_t block_count;
        if(!replica_runtime_blocks_copy(cluster,pointers,&block_count,read_mem))return -2;
        for(size_t j=0;j<block_count;j++){
            uintptr_t block=pointers[j],component;struct ReplicaRuntimeBlock current;
            if(!replica_runtime_block_read(block,cluster,&current,read_mem))return -2;
            unsigned bid=current.ident;uint64_t features=current.features;
            if(!bid)continue; /* Stable identity is assigned before geometry publication. */
            if(out->counts[3]==65536)return -3;
            struct ReplicaHealth *health=&out->health[out->counts[3]++];replica_runtime_source_health(current,health);
            /* A terminal block can have negative health before native cleanup. */
            /* Only these native feature families are serialized as movers.
             * Avoid the mover-index RPM on every armor/weapon block. */
            uintptr_t mover=ident && (features&0x402)?native_mover(block,cluster):0;
            if(mover && ident && (features&0x402)){
                if(out->counts[2]==4096)return -3;
                struct ReplicaMover *r=&out->movers[out->counts[2]++];r->ident=ident;r->block_ident=bid;
                if(!read_mem((void*)(mover+0x1c),&r->accel,8))return -2;
            }
            if(root!=cluster || !ident || !(features&0x90))continue;
            if(out->counts[0]==4096)return -3;
            struct VisualBlock *r=&out->blocks[out->counts[0]++];memset(r,0,sizeof(*r));r->ident=ident;r->block_ident=bid;r->mask=((features&0x10)?1:0)|((features&0x80)?2:0);
            if(!read_mem((void*)(block+0x18),&r->type,4) || !read_mem((void*)(block+0x1c),&r->x,12))return -2;
            if(r->mask&1){if(!read_mem((void*)(block+0x158),&component,8) || !component || !read_mem((void*)component,&r->turret,4))return -2;}
            if(r->mask&2){
                unsigned char hitting;
                if(!read_mem((void*)(block+0x150),&component,8) || !component || !read_mem((void*)component,r->laser,4) || !read_mem((void*)(component+0x24),r->laser+1,8) || !read_mem((void*)(component+0x3c),r->laser+3,8) || !read_mem((void*)(component+0x45),&hitting,1))return -2;
                r->hitting=hitting?1:0;
            }
        }
    }
    qsort(out->health,out->counts[3],sizeof(out->health[0]),compare_health);
    uintptr_t shots[2];float simtime;
    if(!read_mem((char*)zone+0x208,shots,16) || shots[1]<shots[0] || (shots[1]-shots[0])%8 || shots[1]-shots[0]>1000000*8 || !read_mem((char*)zone+0x158,&simtime,4))return -2;
    for(size_t i=0;i<(shots[1]-shots[0])/8 && out->counts[1]<2048;i++){
        uintptr_t shot;double pose[4];float end;
        if(!read_mem((void*)(shots[0]+i*8),&shot,8) || !read_mem((void*)(shot+0x30),pose,32))return -2;
        if(!near_focus(pose[0],pose[1],center,radius))continue;
        struct VisualProjectile *r=&out->shots[out->counts[1]];
        r->x=(float)pose[0];r->y=(float)pose[1];r->vx=(float)pose[2];r->vy=(float)pose[3];
        if(!read_mem((void*)(shot+0x60),&r->angle,4) || !read_mem((void*)(shot+0x1b8),&r->size,4) || !read_mem((void*)(shot+0x1bc),&end,4) || !read_mem((void*)(shot+0x1c0),&r->health,4) || !read_mem((void*)(shot+0x1c4),&r->color,4))return -2;
        r->ttl=end-simtime;if(r->ttl>0)out->counts[1]++;
    }
    /* Stable block IDs provide the same ordering for native lookup and wire validation. */
    qsort(out->movers,out->counts[2],sizeof(out->movers[0]),compare_mover);
    return (int)(out->counts[0]+out->counts[1]+out->counts[2]+out->counts[3]);
}
__declspec(dllexport) int RepopulatedApplyMovers(const struct ReplicaMover *rows,int count){
    int result=replica_mover_rows_valid(rows,count);if(result<0)return result;
    if(count)memcpy(replica_movers,rows,(size_t)count*sizeof(*rows));
    replica_mover_count=count;replica_mover_received=replica_now_millis();replica_mover_window_clear(&replica_mover_window);return count;
}
static bool replica_mover_window_owner(void){
    /* Private campaign field setup publishes its immutable owning update
     * thread before any visual-frame apply. Block::update runs on that same
     * native GameZone::Update thread. Timed rows never cross a draw/RPC path. */
    return atomic_load_explicit(&replica_field_context.console,memory_order_acquire) &&
        replica_field_context.thread==GetCurrentThreadId();
}
__declspec(dllexport) int RepopulatedApplyMoverWindow(const struct ReplicaMover *a,int count_a,double source_a,
                                                    const struct ReplicaMover *b,int count_b,double source_b){
    if(!replica_mover_window_owner())return -4;
    return replica_mover_window_accept(&replica_mover_window,a,count_a,source_a,b,count_b,source_b);
}
static void realtime_mover_update(void *block,bool predict){
    uintptr_t cluster,mover;unsigned ident,bid;uint64_t features;float health;
    if(!read_mem((char*)block+0xb8,&cluster,8) || !cluster || !read_mem((char*)block+0x40,&features,8) || !(features&0x402) || !read_mem((char*)block+0x4c,&health,4) || health<=0)return;
    mover=native_mover((uintptr_t)block,cluster);if(!mover)return;
    if(!predict){
        if(!read_mem((char*)block+0x30,&bid,4))return;ident=RepopulatedClusterIdent((void*)cluster);
        float throttle[2]={0};
        double now=replica_now_millis();bool active=true;
        if(replica_mover_window.valid){
            if(!replica_mover_window_owner())return;
            double target=replica_source_view_time(now,replica_motion_clock_ready?replica_motion_clock_offset:0,replica_presentation_delay);
            active=replica_mover_window_evaluate(&replica_mover_window,ident,bid,target,throttle);
        }else{
            const struct ReplicaMover *row=replica_mover_lookup(replica_movers,replica_mover_count,ident,bid);
            if(now-replica_mover_received<=250 && row){throttle[0]=row->accel;throttle[1]=row->angular;}
        }
        memcpy((void*)(mover+0x1c),throttle,8);
        if(!active)return;
    }
    /* Only the native mover helper is run: no weapon, damage, collision,
     * resource collection or ship construction update is executed here. */
    typedef void (*Update)(void*);static Update update;
    if(!update){
        HMODULE game=GetModuleHandleW(NULL);const unsigned char prefix[]={0x48,0x8b,0xc4,0x53,0x57,0x48,0x81,0xec,0x08,0x01,0,0};
        if(!signature(game,"?moverUpdate@Block@@AEAAXXZ",prefix,sizeof(prefix)))return;
        FARPROC raw=GetProcAddress(game,"?moverUpdate@Block@@AEAAXXZ");memcpy(&update,&raw,8);
    }
    uintptr_t force_target=cluster,parent;double previous_force[2]={0};float previous_torque=0;bool have_force=false;
    if(!predict){
        for(int depth=0;depth<16;depth++){
            if(!read_mem((void*)(force_target+0x178),&parent,8) || !parent)break;
            force_target=parent;
        }
        have_force=read_mem((void*)(force_target+0x50),previous_force,16) && read_mem((void*)(force_target+0x68),&previous_torque,4);
        if(!have_force)return;
    }
    uintptr_t previous=replica_effect_cluster;replica_effect_cluster=cluster;
    uintptr_t command=owned_cluster_command((void*)cluster),serial=0,metadata=0;int energy=0;float cached_energy=0;
    bool has_serial=command && read_mem((void*)(command+0x28),&serial,8) && serial && read_mem((void*)(serial+0x1c),&energy,4);
    bool has_cache=read_mem((void*)(cluster+0x110),&metadata,8) && metadata && read_mem((void*)(metadata+0x1c),&cached_energy,4);
    struct ReplicaThrustAuditContext audit_previous=replica_thrust_audit_begin((uintptr_t)block);
    update(block);replica_thrust_audit_context=audit_previous;replica_effect_cluster=previous;replica_local_mover_updates++;
    if(has_serial)memcpy((void*)(serial+0x1c),&energy,4);
    if(has_cache)memcpy((void*)(metadata+0x1c),&cached_energy,4);
    if(!predict && have_force){memcpy((void*)(force_target+0x50),previous_force,16);memcpy((void*)(force_target+0x68),&previous_torque,4);}
}
__declspec(dllexport) unsigned long long RepopulatedLocalMoverUpdates(void){return replica_local_mover_updates;}
__declspec(dllexport) int RepopulatedApplyRealtimeHealth(void *zone,const struct ReplicaHealth *health,int count){
    initialize();if(!compatible || !zone || count<0 || count>65536 || (count && !health))return -1;
    for(int i=0;i<count;i++)if(!health[i].ident || (i && health[i-1].ident>=health[i].ident) || !isfinite(health[i].health) || health[i].health<0 || health[i].health>1e9f || !isfinite(health[i].growth) || health[i].growth<0 || health[i].growth>1 || !isfinite(health[i].lifetime) || health[i].lifetime < -1 || health[i].lifetime>1e6f)return -2;
    uintptr_t roots[2];if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || roots[1]-roots[0]>4096*8)return -3;
    int applied=0;
    for(size_t i=0;i<(roots[1]-roots[0])/8;i++){
        uintptr_t cluster,vector[2];
        if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0xf0),vector,16) || vector[1]<vector[0] || vector[1]-vector[0]>4096*8)return -3;
        for(size_t j=0;j<(vector[1]-vector[0])/8;j++){
            uintptr_t block;unsigned ident;
            if(!read_mem((void*)(vector[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&ident,4))return -3;
            int lo=0,hi=count;while(lo<hi){int mid=lo+(hi-lo)/2;if(health[mid].ident<ident)lo=mid+1;else hi=mid;}
            if(lo==count || health[lo].ident!=ident)continue;
            memcpy((void*)(block+0x4c),&health[lo].health,4);memcpy((void*)(block+0x48),&health[lo].growth,4);memcpy((void*)(block+0x38),&health[lo].lifetime,4);applied++;
        }
    }
    return applied;
}
__declspec(dllexport) void RepopulatedAuditRealtimeHealth(void *zone,const struct ReplicaHealth *health,int count,double *stats){
    stats[0]=0;stats[1]=0;stats[2]=0;
    uintptr_t pilot=replica_find(zone,0x70000002),vector[2];
    if(!pilot || !health || count<0 || count>65536 || !read_mem((void*)(pilot+0xf0),vector,16) || vector[1]<vector[0] || vector[1]-vector[0]>4096*8)return;
    for(size_t j=0;j<(vector[1]-vector[0])/8;j++){
        uintptr_t block;unsigned bid;float actual;
        if(!read_mem((void*)(vector[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&bid,4) || !read_mem((void*)(block+0x4c),&actual,4))return;
        int lo=0,hi=count;while(lo<hi){int mid=lo+(hi-lo)/2;if(health[mid].ident<bid)lo=mid+1;else hi=mid;}
        /* Membership changes arrive on the geometry channel. Compare stable
         * identities common to this state frame and the current pilot. */
        if(lo==count || health[lo].ident!=bid)continue;
        double error=fabs((double)actual-health[lo].health);stats[0]++;stats[2]=fmax(stats[2],error);
        if(!isfinite(actual) || error>0.0001)stats[1]++;
    }
}
