/* Exact-build startup experiment. Only used with a newly generated private save. */
static int spawn_campaign_fixture(void *zone,void *blueprint,unsigned ident,int faction,float x,float y) {
    initialize();
    if (!compatible || !zone || !blueprint || !ident || !isfinite(x) || !isfinite(y)) return -1;
    uintptr_t existing[2];int matches=0;
    if(!read_mem((char*)zone+0x188,existing,sizeof(existing)) || existing[1]<existing[0] ||
       (existing[1]-existing[0])%8 || existing[1]-existing[0]>4096*8) return -11;
    for(size_t i=0;i<(existing[1]-existing[0])/8;i++) {
        uintptr_t candidate,parent;int current;
        if(!read_mem((void*)(existing[0]+i*8),&candidate,8) ||
           !read_mem((void*)(candidate+0x178),&parent,8)) return -11;
        if(parent || RepopulatedClusterIdent((void*)candidate)!=ident) continue;
        if(!read_mem((void*)(candidate+0x118),&current,4) || current!=faction || ++matches>1) return -12;
    }
    if(matches) return 2; /* Preserve an existing ship when loading a private save. */
    HMODULE game=GetModuleHandleW(NULL);
    const char *clone_name="?clone@BlockCluster@@QEBAPEAU1@XZ";
    const char *add_name="?addToGameZone@BlockCluster@@QEAAXPEAUGameZone@@U?$tvec2@M$0A@@glm@@@Z";
    const unsigned char prefix[]={0x48,0x89,0x5c,0x24,0x08};
    if (!signature(game,clone_name,prefix,sizeof(prefix)) || !signature(game,add_name,prefix,sizeof(prefix))) return -2;
    typedef void* (*Clone)(void*); Clone clone;
    typedef void (*Add)(void*,void*,uint64_t); Add add;
    FARPROC raw=GetProcAddress(game,clone_name); memcpy(&clone,&raw,sizeof(raw));
    raw=GetProcAddress(game,add_name); memcpy(&add,&raw,sizeof(raw));
    void *cluster=clone(blueprint); if (!cluster) return -3;
    uintptr_t blocks[2];
    if (!read_mem((char*)cluster+0xf0,blocks,sizeof(blocks)) || blocks[1]<=blocks[0] ||
        (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>4096*8) return -7;
    const char *faction_name="?setFaction@SerialBlock@@QEAAXH@Z";
    const unsigned char faction_sig[]={0x48,0x8b,0x41,0x10};
    if (!signature(game,faction_name,faction_sig,sizeof(faction_sig))) return -8;
    typedef void (*SetFaction)(void*,int); SetFaction set_faction;
    raw=GetProcAddress(game,faction_name); memcpy(&set_faction,&raw,sizeof(raw));
    for(size_t i=0;i<(blocks[1]-blocks[0])/8;i++) {
        uintptr_t block; if (!read_mem((void*)(blocks[0]+i*8),&block,8)) return -9;
        set_faction((void*)(block+0x18),faction);
    }
    union {float xy[2]; uint64_t packed;} position={.xy={x,y}};
    add(cluster,zone,position.packed);
    uintptr_t command,serial; SIZE_T written;
    if (!read_mem((char*)cluster+0x108,&command,8) || !command ||
        !read_mem((void*)(command+0x28),&serial,8) || !serial) return -4;
    uint64_t flags=1;
    if (!WriteProcessMemory(GetCurrentProcess(),(void*)(serial+8),&ident,4,&written) || written!=4 ||
        !WriteProcessMemory(GetCurrentProcess(),(void*)serial,&flags,8,&written) || written!=8) return -5;
    return RepopulatedClusterIdent(cluster)==ident ? 1 : -6;
}

__declspec(dllexport) int RepopulatedSpawnCampaignFixture(void *zone,void *blueprint,unsigned ident,float x,float y) {
    if(ident!=0x70000001) return -10;
    return spawn_campaign_fixture(zone,blueprint,ident,100,x,y);
}

__declspec(dllexport) int RepopulatedSpawnRemoteFixture(void *zone,void *blueprint,float x,float y) {
    return spawn_campaign_fixture(zone,blueprint,0x70000002,20008,x,y);
}

/* Used only by the isolated lifecycle check, never normal play. */
__declspec(dllexport) int RepopulatedDestroyRemoteFixture(void *zone) {
    initialize();
    HMODULE game=GetModuleHandleW(NULL);
    const char *name="?removeHealth@Block@@QEAAMMPEAU1@H@Z";
    const unsigned char prefix[]={0x48,0x89,0x5c,0x24,0x18};
    if (!compatible || !zone || !signature(game,name,prefix,sizeof(prefix))) return -1;
    uintptr_t vector[2];
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8) return -2;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent,command; float health; int faction;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent ||
            !read_mem((void*)(cluster+0x118),&faction,4) || faction!=20008 ||
            RepopulatedClusterIdent((void*)cluster)!=0x70000002 ||
            !read_mem((void*)(cluster+0x108),&command,8) || !command ||
            !read_mem((void*)(command+0x4c),&health,4) || !isfinite(health) || health<=0) continue;
        typedef float (*Damage)(void*,float,void*,int); Damage damage;
        FARPROC raw=GetProcAddress(game,name); memcpy(&damage,&raw,sizeof(raw));
        return damage((void*)command,health+1,NULL,0)>0 ? 1 : 0;
    }
    return 0;
}
