/* Exact x64 build, simulation-thread only. Removal follows native Clear's
 * remove/kill/deferred-free sequence without clearing unrelated objects. */
struct ReplicaPose { unsigned ident,faction;float x,y,vx,vy,angle,energy,resources,capacity; };
struct ReplicaHealth { unsigned ident;float health,growth,lifetime; };
_Static_assert(sizeof(struct ReplicaPose)==40,"replica pose layout");
_Static_assert(sizeof(struct ReplicaHealth)==16,"replica health layout");
static unsigned long long replica_retained,replica_removed,replica_health_applied;
static bool replica_native_player;
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
__declspec(dllexport) int RepopulatedRemoveReplica(void *zone,unsigned ident) {
    initialize();HMODULE game=GetModuleHandleW(NULL);
    const char *remove_name="?removeFromGameZone@BlockCluster@@QEAAHH@Z";
    const char *free_name="?pool_free_mainthread@BlockCluster@@SAXPEAU1@@Z";
    const char *kill_name="?killRecursive@BlockCluster@@QEAAXH@Z";
    const unsigned char remove_sig[]={0x48,0x89,0x5c,0x24,0x18,0x56};
    const unsigned char free_sig[]={0x48,0x85,0xc9,0x74,0x75,0x53};
    if(!compatible || !zone || !ident || !signature(game,remove_name,remove_sig,sizeof(remove_sig)) ||
       !signature(game,free_name,free_sig,sizeof(free_sig)))return -1;
    uintptr_t cluster=replica_find(zone,ident);if(!cluster)return -2;
    typedef int (*Remove)(void*,int);typedef void (*Kill)(void*,int);typedef void (*Free)(void*);
    Remove remove;Kill kill;Free release;FARPROC raw=GetProcAddress(game,remove_name);memcpy(&remove,&raw,8);
    raw=GetProcAddress(game,kill_name);if(!raw)return -1;memcpy(&kill,&raw,8);
    raw=GetProcAddress(game,free_name);memcpy(&release,&raw,8);
    remove((void*)cluster,-1);kill((void*)cluster,-1);release((void*)cluster);
    struct ReplicaSmoothing *history=replica_history(ident,false);if(history)memset(history,0,sizeof(*history));
    replica_removed++;return 1;
}
__declspec(dllexport) int RepopulatedUpdateReplica(void *zone,const struct ReplicaPose *poses,int count,
                                                const struct ReplicaHealth *health,int blocks) {
    initialize();HMODULE game=GetModuleHandleW(NULL);
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
    for(int k=0;k<blocks;k++)if(!health[k].ident || (k && health[k-1].ident>=health[k].ident) ||
       !isfinite(health[k].health) || (health[k].health!=-1 && (health[k].health<0 || health[k].health>1e9f)) ||
       !isfinite(health[k].growth) || (health[k].growth!=-1 && (health[k].growth<0 || health[k].growth>1)) ||
       !isfinite(health[k].lifetime) || health[k].lifetime < -1 || health[k].lifetime>1e6f)return -4;
    for(int i=0;i<count;i++) {
        const struct ReplicaPose *p=&poses[i];uintptr_t cluster=replica_find(zone,p->ident);
        if(!cluster || !isfinite(p->x) || !isfinite(p->y) || !isfinite(p->vx) || !isfinite(p->vy) || !isfinite(p->angle) ||
           fabsf(p->x)>1e6f || fabsf(p->y)>1e6f || fabsf(p->vx)>10000 || fabsf(p->vy)>10000)return -2;
        union {float xy[2];uint64_t packed;} pos={.xy={p->x+world_visual_center[0],p->y+world_visual_center[1]}},vel={.xy={p->vx,p->vy}};
        replica_correction(p->ident,pos.xy[0],pos.xy[1],p->angle);
        rotation((void*)cluster,p->angle);position((void*)cluster,pos.packed);velocity((void*)cluster,vel.packed);
        uintptr_t command=owned_cluster_command((void*)cluster),serial;
        if(command && p->faction){
            if(!read_mem((void*)(command+0x28),&serial,8) || !serial || !isfinite(p->energy) || p->energy<0 || p->energy>1e9f ||
               !isfinite(p->resources) || p->resources<0 || p->resources>1e9f || !isfinite(p->capacity) || p->capacity<0 || p->capacity>1e9f)return -3;
            int energy=(int)p->energy;memcpy((void*)(serial+0x1c),&energy,4);
            memcpy((void*)(serial+0x14),&p->resources,4);memcpy((void*)(serial+0x18),&p->capacity,4);
            uintptr_t metadata;if(read_mem((void*)(cluster+0x110),&metadata,8) && metadata)memcpy((void*)(metadata+0x1c),&p->energy,4);
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
    return count;
}
__declspec(dllexport) void RepopulatedPaceFrame(void) {
    static LARGE_INTEGER frequency,deadline;static HANDLE timer;
    if(!frequency.QuadPart){QueryPerformanceFrequency(&frequency);timer=CreateWaitableTimerExW(NULL,NULL,2,TIMER_ALL_ACCESS);}
    LARGE_INTEGER now;QueryPerformanceCounter(&now);
    LONGLONG step=frequency.QuadPart/60;
    if(!deadline.QuadPart || now.QuadPart-deadline.QuadPart>step*4)deadline.QuadPart=now.QuadPart;
    if(timer && deadline.QuadPart>now.QuadPart){
        LARGE_INTEGER due;due.QuadPart=-((deadline.QuadPart-now.QuadPart)*10000000/frequency.QuadPart);
        if(SetWaitableTimer(timer,&due,0,NULL,NULL,false))WaitForSingleObject(timer,100);
    }
    deadline.QuadPart+=step;
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
struct NativeWeaponIntent { unsigned ident,mask;float x,y,vx,vy,spread; };
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
_Static_assert(sizeof(struct NativeWeaponIntent)==28,"native weapon intent layout");
__declspec(dllexport) int RepopulatedNativeWeapons(void *zone,int owner,unsigned ident,const struct NativeWeaponIntent *weapons,int count,bool stale) {
    initialize();if(!compatible || !zone || owner!=20008 || ident!=0x70000002 || count<0 || count>256 || (count && !weapons))return -1;
    uintptr_t cluster=replica_find(zone,ident),command,vector[2];int faction;double position[2];
    if(!cluster || !read_mem((void*)(cluster+0x118),&faction,4) || faction!=owner ||
       !read_mem((void*)(cluster+0x108),&command,8) || !command || !read_mem((void*)(cluster+0xf0),vector,16) ||
       vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8 || !read_mem((void*)(cluster+0x30),position,16))return -2;
    /* Expired input must release the current ship even when its weapon IDs
     * changed after damage/respawn. Never keep a stale charge request alive. */
    if(stale)return RepopulatedReleaseWeapons((void*)cluster,owner,ident)<0?-3:0;
    HMODULE game=GetModuleHandleW(NULL);typedef void* (*Construct)(void*,void*);typedef bool (*FireWeapon)(void*,void*);typedef bool (*Set)(void*,uint64_t,bool);
    Construct construct;FireWeapon fire;Set set;FARPROC raw=GetProcAddress(game,"??0FiringData@@QEAA@PEBUBlock@@@Z");memcpy(&construct,&raw,8);
    raw=GetProcAddress(game,"?fireWeapon@Block@@QEAA_NAEAUFiringData@@@Z");memcpy(&fire,&raw,8);
    raw=GetProcAddress(game,"?setEnabled@Block@@QEAA_N_K_N@Z");memcpy(&set,&raw,8);if(!construct || !fire || !set)return -3;
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
        const float *values=&weapons[i].x;for(int j=0;j<5;j++)if(!isfinite(values[j]) || fabsf(values[j])>(j<2?1e6f:j<4?10000:100))return -4;
    }
    if(RepopulatedReleaseWeapons((void*)cluster,owner,ident)<0)return -3;
    int fired=0;
    for(int i=0;i<count;i++) {
        const struct NativeWeaponIntent *w=&weapons[i];_Alignas(16) unsigned char data[0x100]={0};construct(data,(void*)command);
        float target[2]={(float)position[0]+w->x,(float)position[1]+w->y};memcpy(data+8,target,8);memcpy(data+24,&w->vx,8);memcpy(data+52,&w->spread,4);
        if(!stale && w->mask && fire((void*)selected[i],data))fired++;
        /* Preserve the native client's group/charge gating; the host's own
         * block update still decides cooldown, energy, damage and spawning. */
        uint64_t enabled;if(!read_mem((void*)(selected[i]+0x100),&enabled,8))return -4;
        set((void*)selected[i],0x800008e0ULL,false);
        if(!stale && w->mask)set((void*)selected[i],(enabled&w->mask)|(w->mask&0x80000000u),true);
    }
    return fired;
}
