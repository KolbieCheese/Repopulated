/* Test-only spectator focus read. Exact x64 Body render pair +0xd8, zone
 * owner +8 and cluster parent +0x178. Read validation does not pin engine
 * object lifetimes. No game or presentation-cache locks are acquired. */
#ifndef REPOPULATED_COMPARISON_FOCUS_H
#define REPOPULATED_COMPARISON_FOCUS_H
static int replica_comparison_focus(void *zone,unsigned ident,double *out){
    if(!zone || !out || (ident!=0x70000001u && ident!=0x70000002u))return -1;
    uintptr_t vector[2],clusters[4096],match=0;
    if(!read_mem((char*)zone+0x188,vector,sizeof(vector)) || vector[1]<vector[0] ||
       (vector[1]-vector[0])%sizeof(uintptr_t) || vector[1]-vector[0]>sizeof(clusters))return 0;
    size_t count=(vector[1]-vector[0])/sizeof(uintptr_t);
    if(count && !read_mem((void*)vector[0],clusters,count*sizeof(clusters[0])))return 0;
    for(size_t i=0;i<count;i++){
        uintptr_t cluster=clusters[i],owner,parent;
        if(!cluster || !read_mem((void*)(cluster+8),&owner,sizeof(owner)) ||
           !read_mem((void*)(cluster+0x178),&parent,sizeof(parent)))return 0;
        if(parent || owner!=(uintptr_t)zone || RepopulatedClusterIdent((void*)cluster)!=ident)continue;
        if(match)return -2; /* Never choose between two live roots with the same ID. */
        match=cluster;
    }
    if(!match)return 0;
    uintptr_t owner,parent;float render[2];
    if(!read_mem((void*)(match+8),&owner,sizeof(owner)) || owner!=(uintptr_t)zone ||
       !read_mem((void*)(match+0x178),&parent,sizeof(parent)) || parent ||
       RepopulatedClusterIdent((void*)match)!=ident ||
       !read_mem((void*)(match+0xd8),render,sizeof(render)) ||
       !isfinite(render[0]) || !isfinite(render[1]) ||
       fabsf(render[0])>1e9f || fabsf(render[1])>1e9f ||
       RepopulatedClusterIdent((void*)match)!=ident ||
       !read_mem((void*)(match+8),&owner,sizeof(owner)) || owner!=(uintptr_t)zone ||
       !read_mem((void*)(match+0x178),&parent,sizeof(parent)) || parent)return 0;
    /* Actual native coordinates include the client's visual sector offset. */
    out[0]=render[0];out[1]=render[1];return 1;
}
#endif
