/* Exact-build AI::update prefilter. Native AI continues directly through the
 * original trampoline; only an explicitly owned faction/pilot crosses into
 * the JS remote-control callback. The launcher verifies the full exe hash.
 * Recovered AI::update RVA0x74150 reads ai+0x278 -> command+0xb8 -> cluster.
 */
typedef void (*HostAIUpdate)(void*,bool);
struct HostAIOwner {int faction;unsigned pilot;};
_Static_assert(sizeof(struct HostAIOwner)==8,"host AI owner row layout");
struct HostAIConfiguration {
    HostAIUpdate original,owned;
    int count;
    struct HostAIOwner rows[16];
};
static PVOID volatile host_ai_configuration;
static volatile LONG64 host_ai_stats[4];
static bool host_ai_callable(void *pointer){
    MEMORY_BASIC_INFORMATION info;
    if(!pointer || VirtualQuery(pointer,&info,sizeof(info))!=sizeof(info) || info.State!=MEM_COMMIT ||
       (info.Protect&(PAGE_GUARD|PAGE_NOACCESS)))return false;
    DWORD access=info.Protect&0xff;
    return access==PAGE_EXECUTE || access==PAGE_EXECUTE_READ || access==PAGE_EXECUTE_READWRITE || access==PAGE_EXECUTE_WRITECOPY;
}
__declspec(dllexport) int RepopulatedConfigureHostAI(void *original,void *owned,const struct HostAIOwner *rows,int count){
    initialize();
    if(!compatible || !original || !owned || !rows || count<1 || count>16)return -1;
    /* JS verifies the exact native AI entry before replacing it. Gum may
     * return a dispatch trampoline rather than the original first bytes;
     * validate callable mappings without guessing Frida's trampoline layout. */
    if(!host_ai_callable(original) || !host_ai_callable(owned))return -2;
    struct HostAIConfiguration *config=HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,sizeof(*config));
    if(!config)return -3;
    for(int i=0;i<count;i++){
        if(rows[i].faction<=0){HeapFree(GetProcessHeap(),0,config);return -1;}
        for(int j=0;j<i;j++)if(rows[j].faction==rows[i].faction){HeapFree(GetProcessHeap(),0,config);return -1;}
        config->rows[i]=rows[i];
    }
    config->count=count;memcpy(&config->original,&original,sizeof(original));memcpy(&config->owned,&owned,sizeof(owned));
    /* Publish one immutable process-lifetime configuration. Readers never
     * wait or enter a game mutex, and cannot observe a partially copied row. */
    if(InterlockedCompareExchangePointer(&host_ai_configuration,config,NULL)){
        HeapFree(GetProcessHeap(),0,config);return -4;
    }
    return 0;
}
__declspec(dllexport) void RepopulatedHostAIUpdate(void *ai,bool force){
    InterlockedIncrement64(&host_ai_stats[0]);
    const struct HostAIConfiguration *config=InterlockedCompareExchangePointer(&host_ai_configuration,NULL,NULL);
    if(!config){InterlockedIncrement64(&host_ai_stats[3]);return;}
    /* These pointers are live during the original native invocation, whose
     * first instructions dereference the same command and owning cluster.
     * Do not perform kernel ReadProcessMemory calls for every vanilla AI. */
    uintptr_t command=0,cluster=0;
    if(ai)memcpy(&command,(char*)ai+0x278,8);
    if(command)memcpy(&cluster,(void*)(command+0xb8),8);
    if(cluster){
        int faction;memcpy(&faction,(void*)(cluster+0x118),4);
        for(int i=0;i<config->count;i++)if(config->rows[i].faction==faction){
            bool match=!config->rows[i].pilot;
            if(!match){
                uintptr_t serial;unsigned ident=0;
                memcpy(&serial,(void*)(command+0x28),8);
                if(serial)memcpy(&ident,(void*)(serial+8),4);
                match=ident==config->rows[i].pilot;
            }
            if(match){InterlockedIncrement64(&host_ai_stats[2]);config->owned(ai,force);return;}
            break;
        }
    }else InterlockedIncrement64(&host_ai_stats[3]);
    InterlockedIncrement64(&host_ai_stats[1]);config->original(ai,force);
}
__declspec(dllexport) void RepopulatedReadHostAIStats(double *out){
    if(!out)return;
    for(int i=0;i<4;i++)out[i]=(double)InterlockedCompareExchange64(&host_ai_stats[i],0,0);
}
