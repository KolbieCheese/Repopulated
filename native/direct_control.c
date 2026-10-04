/* Included by diagnostic.c. Native navigation control, exact-build research. */
__declspec(dllexport) int RepopulatedDriveAimed(void *zone,int owner,int target,unsigned ident,float x,float y,float aim) {
    initialize();
    if (!compatible || !zone || owner<=0 || owner!=target || !isfinite(x) || !isfinite(y) ||
        fabsf(x)>1 || fabsf(y)>1 || (!isnan(aim) && (!isfinite(aim) || fabsf(aim)>3.142f))) return -1;
    HMODULE game=GetModuleHandleW(NULL);
    const char *get_name="?getCommandAI@Block@@QEAAPEAUAI@@XZ";
    const char *config_name="?getNavConfig@BlockCluster@@QEBAXPEAUsnConfig@@@Z";
    const unsigned char get_sig[]={0x48,0x8b,0x81,0xb8,0,0,0};
    const unsigned char config_sig[]={0x48,0x89,0x5c,0x24,0x10,0x57,0x48,0x83,0xec,0x20};
    const unsigned char update_sig[]={0x40,0x53,0x41,0x56,0x48,0x81,0xec,0xa8,0,0,0};
    uintptr_t update_address=(uintptr_t)game+0x43ef0; unsigned char actual[sizeof(update_sig)];
    if (!signature(game,get_name,get_sig,sizeof(get_sig)) ||
        !signature(game,config_name,config_sig,sizeof(config_sig)) ||
        !read_mem((void*)update_address,actual,sizeof(actual)) || memcmp(actual,update_sig,sizeof(actual))) return -2;
    uintptr_t vector[2],selected=0,command=0;
    if (!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
        (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8) return -3;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent,candidate; int faction;
        if (!read_mem((void*)(vector[0]+i*8),&cluster,8) ||
            !read_mem((void*)(cluster+0x178),&parent,8) || parent ||
            !read_mem((void*)(cluster+0x118),&faction,4) || faction!=target ||
            !read_mem((void*)(cluster+0x108),&candidate,8) || !candidate) continue;
        if(ident && RepopulatedClusterIdent((void*)cluster)!=ident) continue;
        if (selected) return -4;
        selected=cluster; command=candidate;
    }
    if (!selected) return 0;
    typedef void* (*GetAI)(void*); GetAI get_ai;
    FARPROC raw=GetProcAddress(game,get_name); memcpy(&get_ai,&raw,sizeof(raw));
    void *ai=get_ai((void*)command); if (!ai) return -5;
    /* AI::update and playerUpdate both pass AI+0x2d4 to getNavConfig,
       then AI+0x280 to the same native sNav::update function. */
    typedef void (*GetConfig)(void*,void*); GetConfig get_config;
    raw=GetProcAddress(game,config_name); if (!raw) return -6;
    memcpy(&get_config,&raw,sizeof(raw));
    typedef bool (*UpdateNav)(void*); UpdateNav update_nav;
    memcpy(&update_nav,&update_address,sizeof(update_address));
    float angle; if (!read_mem((void*)(selected+0x60),&angle,4) || !isfinite(angle)) return -7;
    float length=sqrtf(x*x+y*y); if (length>1) { x/=length; y/=length; }
    float desired[3]={x*200,y*200,!isnan(aim) ? aim : length>0.001f ? atan2f(y,x) : angle};
    unsigned dimensions=0x106; SIZE_T written;
    get_config((void*)selected,(char*)ai+0x2d4);
    if (!WriteProcessMemory(GetCurrentProcess(),(char*)ai+0x2c4,desired,sizeof(desired),&written) ||
        written!=sizeof(desired) ||
        !WriteProcessMemory(GetCurrentProcess(),(char*)ai+0x2b8,&dimensions,4,&written) || written!=4) return -8;
    update_nav((char*)ai+0x280);
    return 1;
}

__declspec(dllexport) int RepopulatedDriveOwned(void *zone,int owner,int target,unsigned ident,float x,float y) {
    return RepopulatedDriveAimed(zone,owner,target,ident,x,y,NAN);
}
