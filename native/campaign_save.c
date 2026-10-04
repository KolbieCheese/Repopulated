/* Invoke on the game thread with an isolated profile. Publish separately. */
__declspec(dllexport) int RepopulatedCheckpointCampaign(void *zone,void *save,const wchar_t *source,const wchar_t *target) {
    initialize();
    if(!compatible || !zone || !save || !source || !target || wcslen(source)>1024 || wcslen(target)>1024) return -1;
    unsigned char locked;
    if(!read_mem((char*)zone+0x3c8,&locked,1) || locked) return -2;
    HMODULE game=GetModuleHandleW(NULL);uintptr_t streamer,vtable,save_loaded,flush;
    if(!read_mem((char*)zone+0x248,&streamer,8) || !read_mem((void*)streamer,&vtable,8) ||
       !read_mem((void*)(vtable+0x20),&save_loaded,8) || !read_mem((void*)(vtable+0x28),&flush,8) ||
       save_loaded!=(uintptr_t)game+0x222d30 || flush!=(uintptr_t)game+0x222570) return -3;
    uintptr_t write_save=(uintptr_t)game+0x1d9e20,write_blueprints=(uintptr_t)game+0x1d9a40;
    const unsigned char save_prefix[]={0x48,0x89,0x5c,0x24,0x18,0x55,0x56,0x57};
    const unsigned char blueprint_prefix[]={0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18};
    unsigned char actual[sizeof(blueprint_prefix)];
    if(!read_mem((void*)write_save,actual,sizeof(save_prefix)) || memcmp(actual,save_prefix,sizeof(save_prefix)) ||
       !read_mem((void*)write_blueprints,actual,sizeof(blueprint_prefix)) || memcmp(actual,blueprint_prefix,sizeof(blueprint_prefix))) return -4;
    typedef bool (*Save)(void*);typedef void (*Flush)(void*);
    Save sectors,metadata,blueprints;Flush wait;
    memcpy(&sectors,&save_loaded,8);memcpy(&wait,&flush,8);memcpy(&metadata,&write_save,8);memcpy(&blueprints,&write_blueprints,8);
    if(!sectors((void*)streamer)) return -5;
    wait((void*)streamer);
    if(!metadata(save) || !blueprints(save)) return -6;
    if(!CreateDirectoryW(target,NULL)) return -7;
    wchar_t pattern[2048],from[2048],to[2048];
    swprintf(pattern,2048,L"%ls\\*",source);
    WIN32_FIND_DATAW entry;HANDLE search=FindFirstFileW(pattern,&entry);
    if(search==INVALID_HANDLE_VALUE) return -8;
    int count=0,result=0;uint64_t total=0;
    do {
        if(!wcscmp(entry.cFileName,L".") || !wcscmp(entry.cFileName,L"..")) continue;
        if(entry.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY|FILE_ATTRIBUTE_REPARSE_POINT)) {result=-9;break;}
        total+=((uint64_t)entry.nFileSizeHigh<<32)|entry.nFileSizeLow;
        if(++count>4096 || total>256*1024*1024) {result=-10;break;}
        swprintf(from,2048,L"%ls\\%ls",source,entry.cFileName);swprintf(to,2048,L"%ls\\%ls",target,entry.cFileName);
        if(!CopyFileW(from,to,TRUE)) {result=-11;break;}
    } while(FindNextFileW(search,&entry));
    if(!result && GetLastError()!=ERROR_NO_MORE_FILES) result=-12;
    FindClose(search);return result?result:count;
}
