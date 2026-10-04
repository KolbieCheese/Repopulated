/* Included by diagnostic.c. Exact-build, simulation-thread research only. */
struct WorldString { union { char text[16]; const char *pointer; } buffer; size_t size,capacity; };
typedef void* (*WorldSerialize)(void*,void*);
typedef void (*WorldDestroy)(void*);
static char world_load_message[4096];
static float world_visual_center[2];
struct ReplicaSmoothing { unsigned ident;float last_x,last_y,last_angle,dx,dy,da;bool presented;ULONGLONG received; };
static struct ReplicaSmoothing replica_smoothing[4096];
static struct ReplicaSmoothing *replica_history(unsigned ident,bool create) {
    struct ReplicaSmoothing *free_slot=NULL,*oldest=&replica_smoothing[0];
    for(int i=0;i<4096;i++){
        struct ReplicaSmoothing *row=&replica_smoothing[i];if(row->ident==ident)return row;
        if(!row->ident && !free_slot)free_slot=row;
        if(row->received<oldest->received)oldest=row;
    }
    if(!create)return NULL;
    struct ReplicaSmoothing *row=free_slot?free_slot:oldest;memset(row,0,sizeof(*row));row->ident=ident;return row;
}
static void replica_correction(unsigned ident,float x,float y,float angle) {
    struct ReplicaSmoothing *row=replica_history(ident,true);
    row->dx=row->presented?row->last_x-x:0;row->dy=row->presented?row->last_y-y:0;
    row->da=row->presented?remainderf(row->last_angle-angle,6.283185307f):0;row->received=GetTickCount64();
    if(row->dx*row->dx+row->dy*row->dy>1000000){row->dx=0;row->dy=0;row->da=0;}
}
__declspec(dllexport) const char *RepopulatedWorldLoadMessage(void) { return world_load_message; }
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
        if(!isfinite(radius) || radius<100 || radius>10000) return -8;
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
__declspec(dllexport) int RepopulatedPresentReplica(void *zone,float seconds,unsigned pilot,float *pilot_position) {
    initialize();
    if(!compatible || !zone || !pilot_position || !isfinite(seconds) || seconds<0 || seconds>0.25f) return -1;
    HMODULE game=GetModuleHandleW(NULL);
    const char *name="?setRenderPosAngle@Body@@QEAAXU?$tvec2@M$0A@@glm@@M@Z";
    const unsigned char prefix[]={0x48,0x89,0x54,0x24,0x08,0xc5,0xfa,0x10,0x4c,0x24,0x08};
    if(!signature(game,name,prefix,sizeof(prefix))) return -2;
    typedef void (*RenderPose)(void*,uint64_t,float);RenderPose render_pose;
    FARPROC raw=GetProcAddress(game,name);memcpy(&render_pose,&raw,sizeof(raw));
    uintptr_t vector[2];
    if(!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
       (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8) return -3;
    int count=0;bool found=false;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent;double state[4];float angle,angular;
        if(!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
           !read_mem((void*)(cluster+0x178),&parent,8)) return -4;
        if(parent) continue;
        if(!read_mem((void*)(cluster+0x30),state,sizeof(state)) ||
           !read_mem((void*)(cluster+0x60),&angle,4) || !read_mem((void*)(cluster+0x64),&angular,4)) return -4;
        if(!isfinite(angle) || !isfinite(angular) || fabsf(angular)>10000) return -5;
        angle+=angular*seconds;
        for(int j=0;j<4;j++) if(!isfinite(state[j])) return -5;
        union {float xy[2];uint64_t packed;} position={.xy={(float)(state[0]+state[2]*seconds),(float)(state[1]+state[3]*seconds)}};
        unsigned ident=RepopulatedClusterIdent((void*)cluster);
        struct ReplicaSmoothing *history=replica_history(ident,false);
        if(history){
            float blend=fmaxf(0,1-(float)(GetTickCount64()-history->received)/100.0f);
            position.xy[0]+=history->dx*blend;position.xy[1]+=history->dy*blend;angle+=history->da*blend;
            history->last_x=position.xy[0];history->last_y=position.xy[1];history->last_angle=angle;history->presented=true;
        }
        render_pose((void*)cluster,position.packed,angle);count++;
        if(ident==pilot) {
            pilot_position[0]=position.xy[0];pilot_position[1]=position.xy[1];found=true;
        }
    }
    return found?count:0;
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
