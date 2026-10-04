/* Build-specific diagnostic only. No guessed legacy C++ struct bindings.
 * The launching harness verifies the complete executable SHA256; this DLL
 * also verifies short code signatures before reading derived field offsets.
 * Invoke RepopulatedSample only from the game simulation callback thread.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include "monotonic_clock.h"
#include "wire_hex.h"

/* Callers pass only their own Frida-allocated wire buffers. These conversions
 * can run on the message thread: they never inspect or mutate game objects. */
__declspec(dllexport) int RepopulatedDecodeHex(const char *hex,int length,unsigned char *out,int capacity){
    return replica_hex_decode(hex,length,out,capacity);
}
__declspec(dllexport) int RepopulatedEncodeHex(const unsigned char *bytes,int length,char *out,int capacity){
    return replica_hex_encode(bytes,length,out,capacity);
}

static bool checked, compatible;
static FILE *output;
static unsigned samples;
static bool controlled;
static bool read_mem(const void *p, void *out, size_t size) {
    SIZE_T got = 0;
    return p && ReadProcessMemory(GetCurrentProcess(), p, out, size, &got) && got == size;
}
static bool signature(HMODULE game, const char *name, const unsigned char *bytes, size_t size) {
    unsigned char actual[16];
    FARPROC fn = GetProcAddress(game, name);
    return fn && size <= sizeof(actual) && read_mem((void*)fn, actual, size) && !memcmp(actual, bytes, size);
}
static void initialize(void) {
    if (checked) return;
    checked = true;
    HMODULE game = GetModuleHandleW(NULL);
    const unsigned char clusters[] = {0x48,0x8d,0x81,0x88,0x01,0x00,0x00,0xc3};
    const unsigned char faction[] = {0x8b,0x81,0x18,0x01,0x00,0x00,0xc3};
    const unsigned char position[] = {0xc5,0xfb,0x10,0x41,0x30};
    const unsigned char projectiles[] = {0x48,0x8d,0x81,0x08,0x02,0x00,0x00,0xc3};
    compatible = signature(game,
      "?getClusters@GameZone@@QEBAAEBV?$vector@PEAUBlockCluster@@V?$allocator@PEAUBlockCluster@@@std@@@std@@XZ", clusters, sizeof(clusters))
      && signature(game, "?getFaction@BlockCluster@@QEBAHXZ", faction, sizeof(faction))
      && signature(game, "?getPos@Body@@QEBA?AU?$tvec2@M$0A@@glm@@XZ", position, sizeof(position))
      && signature(game, "?getProjectiles@GameZone@@QEBAAEBV?$vector@PEAUProjectile@@V?$allocator@PEAUProjectile@@@std@@@std@@XZ", projectiles, sizeof(projectiles));
    char log[MAX_PATH * 4];
    DWORD length = GetEnvironmentVariableA("REPOPULATED_DIAGNOSTIC_LOG", log, sizeof(log));
    if (length && length < sizeof(log)) output = fopen(log, "w");
    if (output) { fprintf(output, "{\"type\":\"native-loaded\",\"compatible\":%s,\"pid\":%lu}\n", compatible ? "true" : "false", GetCurrentProcessId()); fflush(output); }
}

__declspec(dllexport) void GetApiVersion(int *major, int *minor) {
    if (major) *major = 1;
    if (minor) *minor = 0;
}

__declspec(dllexport) bool CreateAiActions(void *ai) {
    (void)ai;
    initialize();
    return false; /* Leave vanilla AI behavior in place. */
}

#include "host_ai.c"

static uintptr_t owned_cluster_command(void *cluster) {
    uintptr_t command,owner;uint64_t features;
    if(!read_mem((char*)cluster+0x108,&command,8) || !command ||
       !read_mem((void*)(command+0xb8),&owner,8) || owner!=(uintptr_t)cluster ||
       !read_mem((void*)(command+0x40),&features,8) || !(features&1)) return 0;
    return command;
}

__declspec(dllexport) unsigned RepopulatedClusterIdent(void *cluster) {
    initialize();
    uintptr_t command,serial; unsigned ident;
    if (!compatible || !cluster) return 0;
    /* A debris fragment can retain a cached command from another cluster.
       That command's ID is absent from this fragment's serialized blocks. */
    command=owned_cluster_command(cluster);
    if (!command) {
        uintptr_t blocks[2],block;unsigned minimum=0;
        if (!read_mem((char*)cluster+0xf0,blocks,sizeof(blocks)) || blocks[1]<=blocks[0] ||
            (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>4096*8) return 0;
        for(size_t i=0;i<(blocks[1]-blocks[0])/8;i++) {
            if(!read_mem((void*)(blocks[0]+i*8),&block,8) || !read_mem((void*)(block+0x30),&ident,4))return 0;
            if(ident && (!minimum || ident<minimum))minimum=ident;
        }
        return minimum;
    }
    if (!read_mem((void*)(command+0x28),&serial,8) || !serial ||
        !read_mem((void*)(serial+8),&ident,4)) return 0;
    return ident;
}

__declspec(dllexport) unsigned RepopulatedEnsureClusterIdent(void *cluster, unsigned requested) {
    initialize();
    unsigned current=RepopulatedClusterIdent(cluster);
    if (current || !compatible || !requested) return current;
    const unsigned char prefix[]={0x48,0x8b,0x02,0x4c,0x8b,0xc2};
    if (!signature(GetModuleHandleW(NULL),"??8SerialCommand@@QEBA_NAEBU0@@Z",prefix,sizeof(prefix))) return 0;
    uintptr_t command,serial; int faction,serial_faction;
    command=owned_cluster_command(cluster);
    if (!command) {
        uintptr_t blocks[2],block; SIZE_T written=0;
        if (!read_mem((char*)cluster+0xf0,blocks,sizeof(blocks)) || blocks[1]<=blocks[0] ||
            !read_mem((void*)blocks[0],&block,8) ||
            !WriteProcessMemory(GetCurrentProcess(),(void*)(block+0x30),&requested,4,&written) || written!=4) return 0;
        return RepopulatedClusterIdent(cluster);
    }
    if (
        !read_mem((void*)(command+0x28),&serial,8) || !serial ||
        !read_mem((char*)cluster+0x118,&faction,4) ||
        !read_mem((void*)(serial+0x10),&serial_faction,4) || faction!=serial_faction) return 0;
    SIZE_T written=0;
    if (!WriteProcessMemory(GetCurrentProcess(),(void*)(serial+8),&requested,4,&written) || written!=4) return 0;
    return RepopulatedClusterIdent(cluster);
}

/* Research waypoint control. Resolve current objects on the simulation thread;
 * never hold a cluster pointer across callbacks. The caller supplies ownership.
 * This controls vanilla navigation, not the complete human input interface. */
__declspec(dllexport) int RepopulatedMoveOwned(void *zone, int owner_faction,
                                             int target_faction, float x, float y) {
    initialize();
    if (!compatible || !zone || owner_faction <= 0 || owner_faction != target_faction ||
        !isfinite(x) || !isfinite(y) || fabsf(x) > 1000000 || fabsf(y) > 1000000) return -1;
    HMODULE game = GetModuleHandleW(NULL);
    const char *get_name = "?getCommandAI@Block@@QEAAPEAUAI@@XZ";
    const char *clear_name = "?clearCommands@AI@@QEAAXXZ";
    const char *move_name = "?appendCommandDest@AI@@QEAAXU?$tvec2@M$0A@@glm@@M@Z";
    const unsigned char get_sig[] = {0x48,0x8b,0x81,0xb8,0,0,0};
    const unsigned char clear_sig[] = {0x48,0x89,0x5c,0x24,0x10};
    const unsigned char move_sig[] = {0x40,0x53,0x48,0x83,0xec,0x40};
    if (!signature(game, get_name, get_sig, sizeof(get_sig)) ||
        !signature(game, clear_name, clear_sig, sizeof(clear_sig)) ||
        !signature(game, move_name, move_sig, sizeof(move_sig))) return -2;
    typedef void* (*GetAI)(void*);
    typedef void (*Clear)(void*);
    typedef void (*Move)(void*, uint64_t, float);
    GetAI get_ai; Clear clear; Move move;
    FARPROC raw = GetProcAddress(game, get_name); memcpy(&get_ai, &raw, sizeof(raw));
    raw = GetProcAddress(game, clear_name); memcpy(&clear, &raw, sizeof(raw));
    raw = GetProcAddress(game, move_name); memcpy(&move, &raw, sizeof(raw));
    uintptr_t vector[3];
    if (!read_mem((char*)zone + 0x188, vector, sizeof(vector)) || vector[1] < vector[0] ||
        vector[2] < vector[1] || (vector[1]-vector[0]) % 8 || vector[2]-vector[0] > 1000000*8) return -3;
    unsigned count = (unsigned)((vector[1]-vector[0])/8), applied = 0;
    union { float xy[2]; uint64_t packed; } desired = {.xy={x,y}};
    for (unsigned i=0; i<count && i<4096; i++) {
        uintptr_t cluster, parent, command; int faction;
        if (!read_mem((void*)(vector[0]+i*8), &cluster, 8) ||
            !read_mem((void*)(cluster+0x118), &faction, 4) || faction != target_faction ||
            !read_mem((void*)(cluster+0x178), &parent, 8) || parent ||
            !read_mem((void*)(cluster+0x108), &command, 8) || !command) continue;
        void *ai = get_ai((void*)command);
        if (!ai) continue;
        clear(ai); move(ai, desired.packed, 10.0f); applied++;
    }
    if (output) { fprintf(output,"{\"type\":\"waypoint-applied\",\"thread\":%lu,\"ownerFaction\":%d,\"targetFaction\":%d,\"x\":%.3f,\"y\":%.3f,\"ships\":%u}\n",GetCurrentThreadId(),owner_faction,target_faction,x,y,applied); fflush(output); }
    return (int)applied;
}

/* Fixed-fixture replication only: exactly one root ship per faction. Refuse
 * ambiguous mappings. Complete block/world replication is not provided. */
__declspec(dllexport) int RepopulatedApplyPose(void *zone, int faction,
                                            unsigned ident, float x, float y, float vx, float vy, float angle) {
    initialize();
    if (!compatible || !zone || faction <= 0 || !isfinite(x) || !isfinite(y) ||
        !isfinite(vx) || !isfinite(vy) || !isfinite(angle) || fabsf(angle)>10000 || fabsf(x)>1000000 || fabsf(y)>1000000 ||
        fabsf(vx)>10000 || fabsf(vy)>10000) return -1;
    HMODULE game = GetModuleHandleW(NULL);
    const char *pos_name = "?setPos@BlockCluster@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
    const char *vel_name = "?setVel@Body@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
    const char *angle_name = "?setAngle@BlockCluster@@QEAAXM@Z";
    const unsigned char pos_sig[] = {0x48,0x89,0x5c,0x24,0x18};
    const unsigned char vel_sig[] = {0x48,0x89,0x54,0x24,0x08};
    const unsigned char angle_sig[] = {0x40,0x53,0x48,0x83,0xec,0x30};
    if (!signature(game,pos_name,pos_sig,sizeof(pos_sig)) ||
        !signature(game,vel_name,vel_sig,sizeof(vel_sig)) ||
        !signature(game,angle_name,angle_sig,sizeof(angle_sig))) return -2;
    uintptr_t vector[3], selected=0;
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        vector[2]<vector[1] || (vector[1]-vector[0])%8 || vector[2]-vector[0]>1000000*8) return -3;
    size_t count=(vector[1]-vector[0])/8;
    for (size_t i=0; i<count && i<4096; i++) {
        uintptr_t cluster,parent,command; int current;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x118),&current,4) || current!=faction ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent ||
            !read_mem((void*)(cluster+0x108),&command,8) || !command) continue;
        if (ident && RepopulatedClusterIdent((void*)cluster)!=ident) continue;
        if (selected) return -4;
        selected=cluster;
    }
    if (!selected) return 0;
    typedef void (*Setter)(void*,uint64_t);
    typedef void (*AngleSetter)(void*,float);
    Setter position,velocity; FARPROC raw=GetProcAddress(game,pos_name);
    memcpy(&position,&raw,sizeof(raw)); raw=GetProcAddress(game,vel_name);
    memcpy(&velocity,&raw,sizeof(raw));
    AngleSetter rotation; raw=GetProcAddress(game,angle_name);
    if (!raw) return -2;
    memcpy(&rotation,&raw,sizeof(raw));
    union { float xy[2]; uint64_t packed; } p={.xy={x,y}},v={.xy={vx,vy}};
    rotation((void*)selected,angle); position((void*)selected,p.packed); velocity((void*)selected,v.packed);
    double actual[4];
    if (!read_mem((void*)(selected+0x30),actual,sizeof(actual))) return -5;
    if (fabs(actual[0]-x)>0.1 || fabs(actual[1]-y)>0.1 ||
        fabs(actual[2]-vx)>0.1 || fabs(actual[3]-vy)>0.1) return -6;
    float actual_angle;
    if (!read_mem((void*)(selected+0x60),&actual_angle,sizeof(actual_angle)) ||
        fabsf(remainderf(actual_angle-angle,6.283185307f))>0.1f) return -7;
    return 1;
}

/* Constructor and firing API derived from this build's exported machine code.
 * FiringData's observed writes end at +0x51; reserve aligned 0x100 bytes without
 * importing a legacy C++ definition. No owning field/destructor is involved. */
static int fire_owned(void *zone, int owner_faction,
                      int target_faction, unsigned ident, float x, float y) {
    initialize();
    if (!compatible || !zone || owner_faction<=0 || owner_faction!=target_faction ||
        !isfinite(x) || !isfinite(y) || fabsf(x)>1000000 || fabsf(y)>1000000) return -1;
    HMODULE game=GetModuleHandleW(NULL);
    const char *ctor_name="??0FiringData@@QEAA@PEBUBlock@@@Z";
    const char *fire_name="?fireWeaponsAt@AI@@QEAAHAEAUFiringData@@@Z";
    const char *get_name="?getCommandAI@Block@@QEAAPEAUAI@@XZ";
    const unsigned char ctor_sig[]={0x48,0x89,0x5c,0x24,0x08};
    const unsigned char fire_sig[]={0x48,0x89,0x5c,0x24,0x18};
    const unsigned char get_sig[]={0x48,0x8b,0x81,0xb8,0,0,0};
    if (!signature(game,ctor_name,ctor_sig,sizeof(ctor_sig)) ||
        !signature(game,fire_name,fire_sig,sizeof(fire_sig)) ||
        !signature(game,get_name,get_sig,sizeof(get_sig))) return -2;
    typedef void* (*GetAI)(void*);
    typedef void* (*Construct)(void*,void*);
    typedef int (*Fire)(void*,void*);
    GetAI get_ai; Construct construct; Fire fire;
    FARPROC raw=GetProcAddress(game,get_name); memcpy(&get_ai,&raw,sizeof(raw));
    raw=GetProcAddress(game,ctor_name); memcpy(&construct,&raw,sizeof(raw));
    raw=GetProcAddress(game,fire_name); memcpy(&fire,&raw,sizeof(raw));
    uintptr_t vector[3];
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        vector[2]<vector[1] || (vector[1]-vector[0])%8 || vector[2]-vector[0]>1000000*8) return -3;
    size_t count=(vector[1]-vector[0])/8; int fired=0;
    for (size_t i=0;i<count && i<4096;i++) {
        uintptr_t cluster,parent,command; int faction;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x118),&faction,4) || faction!=target_faction ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent ||
            !read_mem((void*)(cluster+0x108),&command,8) || !command) continue;
        if (ident && RepopulatedClusterIdent((void*)cluster)!=ident) continue;
        void *ai=get_ai((void*)command);
        if (!ai) continue;
        _Alignas(16) unsigned char data[0x100]={0};
        construct(data,(void*)command);
        float target[2]={x,y}; memcpy(data+8,target,sizeof(target));
        fired+=fire(ai,data);
        /* Firing can insert missile clusters and reallocate the vector. Never
         * continue through the captured vector after invoking it. This fixture
         * targets the first matching command ship only. */
        break;
    }
    if (output && fired) { fprintf(output,"{\"type\":\"weapon-fire\",\"thread\":%lu,\"faction\":%d,\"fired\":%d}\n",GetCurrentThreadId(),owner_faction,fired); fflush(output); }
    return fired;
}

__declspec(dllexport) int RepopulatedFireOwned(void *zone,int owner,int target,float x,float y) {
    return fire_owned(zone,owner,target,0,x,y);
}
__declspec(dllexport) int RepopulatedFireShip(void *zone,int owner,int target,unsigned ident,float x,float y) {
    if (!ident) return -1;
    return fire_owned(zone,owner,target,ident,x,y);
}

/* Native charging weapons fire on release. Clear their held enables through
 * the verified engine setter; leave chargeTime and laser decay to the game. */
__declspec(dllexport) int RepopulatedReleaseWeapons(void *cluster,int owner,unsigned ident) {
    initialize();if(!compatible || !cluster || owner<=0 || !ident)return -1;
    int faction;uintptr_t parent,blocks[2];
    if(!read_mem((char*)cluster+0x118,&faction,4) || faction!=owner || RepopulatedClusterIdent(cluster)!=ident ||
       !read_mem((char*)cluster+0x178,&parent,8) || parent || !read_mem((char*)cluster+0xf0,blocks,16) ||
       blocks[1]<blocks[0] || (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>4096*8)return -2;
    HMODULE game=GetModuleHandleW(NULL);const char *name="?setEnabled@Block@@QEAA_N_K_N@Z";
    const unsigned char prefix[]={0x48,0x8b,0x81,0x00,0x01,0x00,0x00,0x45,0x84,0xc0};
    if(!signature(game,name,prefix,sizeof(prefix)))return -3;
    typedef bool (*SetEnabled)(void*,uint64_t,bool);SetEnabled set;FARPROC raw=GetProcAddress(game,name);memcpy(&set,&raw,8);
    int count=0;
    for(size_t i=0;i<(blocks[1]-blocks[0])/8;i++) {
        uintptr_t block;uint64_t features;
        if(!read_mem((void*)(blocks[0]+i*8),&block,8) || !read_mem((void*)(block+0x40),&features,8))return -4;
        uint64_t mask=features&0x800008e0ULL;if(mask){set((void*)block,mask,false);count++;}
    }
    return count;
}

/* Export actual engine serialization; preserve arbitrary bytes. The native
 * std::string return layout and cleanup call are observed in toString itself. */
__declspec(dllexport) int RepopulatedExportCluster(void *zone, int faction) {
    initialize();
    if (!compatible || !zone || faction<=0) return -1;
    HMODULE game=GetModuleHandleW(NULL);
    const char *name="?toString@BlockCluster@@QEBA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ";
    const unsigned char prefix[]={0x48,0x89,0x5c,0x24,0x08};
    if (!signature(game,name,prefix,sizeof(prefix))) return -2;
    uintptr_t vector[3],selected=0;
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        vector[2]<vector[1] || (vector[1]-vector[0])%8 || vector[2]-vector[0]>1000000*8) return -3;
    size_t count=(vector[1]-vector[0])/8;
    for (size_t i=0;i<count && i<4096;i++) {
        uintptr_t cluster,command,parent; int current;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x118),&current,4) || current!=faction ||
            !read_mem((void*)(cluster+0x108),&command,8) || !command ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent) continue;
        if (selected) return -4;
        selected=cluster;
    }
    if (!selected) return 0;
    char path[MAX_PATH*4];
    DWORD path_size=GetEnvironmentVariableA("REPOPULATED_CLUSTER_EXPORT",path,sizeof(path));
    if (!path_size || path_size>=sizeof(path)) return -5;
    struct NativeString { union { char text[16]; char *pointer; } buffer; size_t size,capacity; } text={0};
    _Static_assert(sizeof(text)==32,"Observed native std::string size");
    typedef void* (*Serialize)(void*,void*);
    typedef void (*Destroy)(void*);
    Serialize serialize; FARPROC raw=GetProcAddress(game,name); memcpy(&serialize,&raw,sizeof(raw));
    /* toString calls this exact cleanup on its own std::string temporary. */
    uintptr_t destroy_address=(uintptr_t)game+0x1e3e0;
    const unsigned char destroy_sig[]={0x40,0x53,0x48,0x83,0xec,0x20};
    unsigned char observed[sizeof(destroy_sig)];
    if (!read_mem((void*)destroy_address,observed,sizeof(observed)) ||
        memcmp(observed,destroy_sig,sizeof(observed))) return -2;
    Destroy destroy; memcpy(&destroy,&destroy_address,sizeof(destroy));
    serialize((void*)selected,&text);
    int result=-6;
    if (text.size && text.size<=2*1024*1024 && text.capacity>=text.size) {
        const char *data=text.capacity>15 ? text.buffer.pointer : text.buffer.text;
        FILE *file=fopen(path,"wb");
        if (file) { size_t written=fwrite(data,1,text.size,file); fclose(file);
            if (written==text.size) result=(int)written; }
    }
    destroy(&text);
    return result;
}

#include "world_snapshot.c"
#include "presentation.c"
#include "direct_control.c"
#include "campaign_fixture.c"
#include "campaign_save.c"
#include "persistent_replica.c"
#include "replica_motion.c"
#include "realtime_state.c"
#include "pilot_prediction.c"
#include "campaign_map.c"

/* Replica simulation suppression stays in native code. A JavaScript callback
 * per block/AI update made large scenes needlessly expensive. */
__declspec(dllexport) bool RepopulatedReplicaBlockUpdate(void *block,unsigned flags) {
    (void)flags;uintptr_t cluster=0;read_mem((char*)block+0xb8,&cluster,8);
    realtime_mover_update(block,replica_prediction_active(cluster));return false;
}
__declspec(dllexport) void RepopulatedReplicaAIUpdate(void *ai,bool force) {
    (void)force;replica_player_update(ai);
}
__declspec(dllexport) void RepopulatedReplicaPhysicsStep(void *space,double delta) {
    replica_predict_step(space,delta);
}

/* Session bootstrap needs the roster length, not diagnostic per-ship dumps.
 * Keep the full sampler opt-in so retrying a gated import does not repeatedly
 * scan the scene and flush a diagnostic file on the native update thread. */
__declspec(dllexport) int RepopulatedCountNativeClusters(void *zone){
    initialize();if(!compatible || !zone)return -1;
    uintptr_t vector[3];
    if(!read_mem((char*)zone+0x188,vector,sizeof(vector)))return -2;
    if(vector[1]<vector[0] || vector[2]<vector[1] || (vector[1]-vector[0])%sizeof(void*) ||
       vector[2]-vector[0]>1000000*sizeof(void*))return -3;
    return (int)((vector[1]-vector[0])/sizeof(void*));
}

__declspec(dllexport) int RepopulatedSample(void *zone) {
    initialize();
    if (!compatible || !zone || !output) return -1;
    uintptr_t vector[3];
    if (!read_mem((char*)zone + 0x188, vector, sizeof(vector))) return -2;
    if (vector[1] < vector[0] || vector[2] < vector[1] || (vector[1] - vector[0]) % sizeof(void*) ||
        vector[2] - vector[0] > 1000000 * sizeof(void*)) return -3;
    size_t count = (vector[1] - vector[0]) / sizeof(void*);
    uintptr_t shots[3]; int projectile_count=-1;
    if (read_mem((char*)zone+0x208,shots,sizeof(shots)) && shots[1]>=shots[0] &&
        shots[2]>=shots[1] && !((shots[1]-shots[0])%8) && shots[2]-shots[0]<=1000000*8)
        projectile_count=(int)((shots[1]-shots[0])/8);
    fprintf(output, "{\"type\":\"zone\",\"sample\":%u,\"thread\":%lu,\"zone\":\"%p\",\"clusters\":%zu,\"projectiles\":%d,\"ships\":[", samples++, GetCurrentThreadId(), zone, count, projectile_count);
    unsigned emitted = 0, commanders = 0;
    uintptr_t controlled_address = 0;
    double controlled_before[2] = {0}, controlled_after[2] = {0};
    for (size_t i = 0; i < count && i < 4096; i++) {
        uintptr_t cluster, parent, command;
        int faction;
        double position[2], velocity[2];
        if (!read_mem((void*)(vector[0] + i * sizeof(void*)), &cluster, sizeof(cluster)) ||
            !read_mem((void*)(cluster + 0x118), &faction, sizeof(faction)) ||
            !read_mem((void*)(cluster + 0x178), &parent, sizeof(parent)) ||
            !read_mem((void*)(cluster + 0x108), &command, sizeof(command))) continue;
        if (faction == 0) continue;
        /* Body::getPos reads this+0x30; getBody returns the embedded cpBody,
         * not the Body base object. Keep those two addresses distinct. */
        uintptr_t body = parent ? parent : cluster;
        if (!read_mem((void*)(body + 0x30), position, sizeof(position)) ||
            !read_mem((void*)(body + 0x40), velocity, sizeof(velocity)) ||
            !isfinite(position[0]) || !isfinite(position[1]) || !isfinite(velocity[0]) || !isfinite(velocity[1])) continue;
        if (emitted < 8) fprintf(output, "%s{\"address\":\"%p\",\"faction\":%d,\"hasCommand\":%s,\"x\":%.6f,\"y\":%.6f,\"vx\":%.6f,\"vy\":%.6f}", emitted++ ? "," : "", (void*)cluster, faction, command ? "true" : "false", position[0], position[1], velocity[0], velocity[1]);
        if (command && !parent) {
            commanders++;
            if (commanders == 2 && !controlled && GetEnvironmentVariableA("REPOPULATED_TEST_CONTROL", NULL, 0)) {
                const unsigned char prefix[] = {0x48,0x89,0x54,0x24,0x08};
                HMODULE game = GetModuleHandleW(NULL);
                const char *name = "?setVel@Body@@QEAAXU?$tvec2@M$0A@@glm@@@Z";
                if (signature(game, name, prefix, sizeof(prefix))) {
                    typedef void (*SetVelocity)(void*, uint64_t);
                    union { float xy[2]; uint64_t packed; } desired;
                    desired.xy[0] = 30.0f; desired.xy[1] = 0.0f;
                    controlled_before[0] = velocity[0]; controlled_before[1] = velocity[1];
                    FARPROC raw = GetProcAddress(game, name);
                    SetVelocity setter;
                    _Static_assert(sizeof(setter) == sizeof(raw), "x64 function pointer width");
                    memcpy(&setter, &raw, sizeof(setter));
                    setter((void*)body, desired.packed);
                    controlled = read_mem((void*)(body + 0x40), controlled_after, sizeof(controlled_after)) &&
                        controlled_after[0] == 30.0 && controlled_after[1] == 0.0;
                    if (controlled) controlled_address = cluster;
                }
            }
        }
    }
    fprintf(output, "],\"secondShipControlApplied\":%s}\n", controlled ? "true" : "false"); fflush(output);
    if (controlled_address) {
        fprintf(output, "{\"type\":\"control-verified\",\"thread\":%lu,\"address\":\"%p\",\"before\":[%.6f,%.6f],\"after\":[%.6f,%.6f]}\n", GetCurrentThreadId(), (void*)controlled_address, controlled_before[0], controlled_before[1], controlled_after[0], controlled_after[1]);
        fflush(output);
    }
    return (int)count;
}
