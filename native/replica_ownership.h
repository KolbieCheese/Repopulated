/* Network ownership follows the serialized owned command, not the mutable
 * native cluster faction cache. Commandless fragments serialize as neutral. */
#ifndef REPOPULATED_REPLICA_OWNERSHIP_H
#define REPOPULATED_REPLICA_OWNERSHIP_H
static volatile LONG64 replica_ownership_counts[4];
__declspec(dllexport) void RepopulatedOwnershipStats(double *out){
    if(out)for(int i=0;i<4;i++)out[i]=(double)InterlockedCompareExchange64(&replica_ownership_counts[i],0,0);
}
static bool replica_serial_owner(uintptr_t serial,unsigned ident,int *faction){
    unsigned char fields[12];unsigned serial_ident;int value;
    if(!serial || !ident || !faction || !read_mem((void*)(serial+8),fields,sizeof(fields)))return false;
    memcpy(&serial_ident,fields,4);memcpy(&value,fields+8,4);
    if(serial_ident!=ident || value<0)return false;
    *faction=value;return true;
}
static int replica_commandless_owner(void){return 0;}
#endif
