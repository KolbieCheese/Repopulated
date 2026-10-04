/* Exact-build native galaxy map replication. Evidence: getMetaZone 0x5ebb0,
 * GalaxyMap::getIndex 0xa6dd0, map texture 0xa75b0, markVisited 0xfea60,
 * region lookup 0xfb300. All calls occur on the owning game thread.
 * The harness gates the complete executable SHA-256 before loading this DLL.
 */
struct CampaignMapCell { int region; float centrality; unsigned char visited,valid,center,pad; };
struct CampaignMapRegion { int ident; unsigned color; int faction; };
struct CampaignMapObjective { unsigned key,ident; int faction; unsigned flags; float x,y,dx,dy,radius; };
_Static_assert(sizeof(struct CampaignMapObjective)==36,"objective wire layout");
struct CampaignObjectiveRef { uintptr_t pointer; unsigned key; };
static struct CampaignObjectiveRef campaign_objectives[8192];
static unsigned campaign_objective_count=0,campaign_objective_next=1;
static unsigned campaign_objective_key(uintptr_t pointer){
    for(unsigned i=0;i<campaign_objective_count;i++)if(campaign_objectives[i].pointer==pointer)return campaign_objectives[i].key;
    if(campaign_objective_count>=8192)return 0;
    unsigned key=campaign_objective_next++;campaign_objectives[campaign_objective_count++]=(struct CampaignObjectiveRef){pointer,key};return key;
}
_Static_assert(sizeof(struct CampaignMapCell)==12,"native map cell size");
static int campaign_shared_exploration=0,remote_map_width=0;
static float remote_map_radius[2];
static uintptr_t remote_map_meta=0;
static struct CampaignMapCell *remote_map_cells=NULL;
static volatile LONG remote_discovery_thread=0;
typedef void (*CampaignNotify)(void*,void*);
static CampaignNotify campaign_notify_original=NULL;
__declspec(dllexport) int RepopulatedSetSharedExploration(int shared){
    if(shared!=0 && shared!=1)return -1;campaign_shared_exploration=shared;return 1;
}
__declspec(dllexport) void RepopulatedSetMapNotifyOriginal(void *original){
    memcpy(&campaign_notify_original,&original,sizeof(original));
}
__declspec(dllexport) void RepopulatedCampaignMapNotify(void *notifier,void *event){
    /* Shadow discovery belongs to the remote faction. Its native notification
       must not award exploration progress to the host's singleton Player. */
    if((DWORD)InterlockedCompareExchange(&remote_discovery_thread,0,0)==GetCurrentThreadId()){
        uint64_t type;if(read_mem(event,&type,8) && type==0x800)return;
    }
    if(campaign_notify_original)campaign_notify_original(notifier,event);
}
static uintptr_t campaign_map(void *zone,int *width,float radius[2],uintptr_t *cells) {
    const unsigned char get_sig[]={0x48,0x8b,0x81,0x50,0x02,0,0,0xc3};
    HMODULE game=GetModuleHandleW(NULL);uintptr_t meta,vec[3];
    if(!compatible || !zone || !signature(game,"?getMetaZone@GameZone@@QEAAPEAUMetaZone@@XZ",get_sig,sizeof(get_sig)) ||
       !read_mem((char*)zone+0x250,&meta,8) || !meta || !read_mem((void*)(meta+0x10),radius,8) ||
       !read_mem((void*)(meta+0x18),width,4) || !read_mem((void*)(meta+0x20),vec,24) ||
       *width<1 || *width>256 || !isfinite(radius[0]) || !isfinite(radius[1]) ||
       radius[0]<=0 || radius[1]<=0 || radius[0]>1e6f || radius[1]>1e6f ||
       vec[1]<vec[0] || vec[2]<vec[1] || vec[1]-vec[0]!=(uintptr_t)(*width)*(*width)*12 ||
       vec[2]-vec[0]>65536*12)return 0;
    *cells=vec[0];return meta;
}
static uintptr_t campaign_region(uintptr_t meta,int ident) {
    if(!ident)return 0;HMODULE game=GetModuleHandleW(NULL);uintptr_t root;
    const unsigned char sig[]={0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10};
    if(!read_mem((void*)(meta+8),&root,8) || !root || memcmp((char*)game+0xfb300,sig,sizeof(sig)))return 0;
    typedef void *(*Lookup)(void*,int);Lookup lookup=(Lookup)((char*)game+0xfb300);
    return (uintptr_t)lookup((void*)root,ident);
}
static int remote_campaign_map(void *zone){
    int width;float radius[2];uintptr_t cells,meta=campaign_map(zone,&width,radius,&cells);
    if(!meta)return 0;
    if(remote_map_cells && (meta!=remote_map_meta || width!=remote_map_width || memcmp(radius,remote_map_radius,8)))return 0;
    if(!remote_map_cells){
        remote_map_cells=calloc(width*width,sizeof(*remote_map_cells));if(!remote_map_cells)return 0;
        remote_map_width=width;remote_map_meta=meta;memcpy(remote_map_radius,radius,8);
    }
    for(int i=0;i<width*width;i++){
        unsigned char visited=remote_map_cells[i].visited;
        if(!read_mem((void*)(cells+i*12),&remote_map_cells[i],12))return 0;
        remote_map_cells[i].visited=visited;
    }
    return 1;
}
__declspec(dllexport) int RepopulatedRestoreRemoteCampaignMap(void *zone,const struct CampaignMapCell *rows,int count){
    initialize();if(!rows || !remote_campaign_map(zone) || count!=remote_map_width*remote_map_width)return -1;
    for(int i=0;i<count;i++)if(rows[i].visited>1)return -2;
    for(int i=0;i<count;i++)remote_map_cells[i].visited=rows[i].visited;
    return count;
}
__declspec(dllexport) int RepopulatedExploreRemoteMap(void *zone) {
    initialize();int width;float radius[2];uintptr_t cells,meta=campaign_map(zone,&width,radius,&cells);
    uintptr_t ship=replica_find(zone,0x70000002);int faction;
    const unsigned char sig[]={0x48,0x8b,0xc4,0x48,0x89,0x58,0x18,0x55,0x56,0x57};
    HMODULE game=GetModuleHandleW(NULL);
    if(!meta || !ship || !read_mem((void*)(ship+0x118),&faction,4) || faction!=20008 ||
       memcmp((char*)game+0xfea60,sig,sizeof(sig)))return -1;
    double pos[2];if(!read_mem((void*)(ship+0x30),pos,16) || !isfinite(pos[0]) || !isfinite(pos[1]) ||
       fabs(pos[0])>1e6 || fabs(pos[1])>1e6)return -2;
    union {float xy[2];uint64_t packed;} point={.xy={(float)pos[0],(float)pos[1]}};
    typedef int (*Mark)(void*,uint64_t,float);Mark mark=(Mark)((char*)game+0xfea60);
    if(!remote_campaign_map(zone))return -3;
    /* MiniMap::render (0xa77f0) passes twice the float at 0x3cf3e8
       to markVisited. Use that same game setting for the remote faction. */
    float distance;
    if(!read_mem((char*)game+0x3cf3e8,&distance,4) || !isfinite(distance) || distance<=0 || distance>500000)return -5;
    float vision=2*distance;
    if(campaign_shared_exploration){
        int merged=0;
        for(int i=0;i<width*width;i++)if(remote_map_cells[i].visited && !((struct CampaignMapCell*)cells)[i].visited){
            ((struct CampaignMapCell*)cells)[i].visited=1;merged++;
        }
        if(merged){unsigned version;memcpy(&version,(void*)(meta+0x38),4);version++;memcpy((void*)(meta+0x38),&version,4);}
        int marked=mark((void*)meta,point.packed,vision);
        for(int i=0;i<width*width;i++)remote_map_cells[i].visited=((struct CampaignMapCell*)cells)[i].visited;
        return marked+merged;
    }
    if(!campaign_notify_original)return -4;
    /* markVisited only reads this prefix, its copied map vector and wrap flag.
       The real MetaZone, its discovery bits and render version stay untouched. */
    _Alignas(16) unsigned char shadow[0x70];
    if(!read_mem((void*)meta,shadow,sizeof(shadow)))return -3;
    uintptr_t vector[3]={(uintptr_t)remote_map_cells,(uintptr_t)(remote_map_cells+width*width),(uintptr_t)(remote_map_cells+width*width)};
    memcpy(shadow+0x20,vector,sizeof(vector));
    InterlockedExchange(&remote_discovery_thread,(LONG)GetCurrentThreadId());
    int marked=mark(shadow,point.packed,vision);InterlockedExchange(&remote_discovery_thread,0);
    return marked;
}
static int export_campaign_map(void *zone,const char *path,const struct CampaignMapCell *override) {
    initialize();int width;float radius[2];uintptr_t cells,meta=campaign_map(zone,&width,radius,&cells);
    if(!meta || !path)return -1;
    struct CampaignMapCell *rows=malloc(width*width*12);if(!rows)return -2;
    if(!read_mem(override?(void*)override:(void*)cells,rows,width*width*12)){free(rows);return -2;}
    struct CampaignMapRegion regions[1024];int region_count=0;
    for(int i=0;i<width*width;i++){
        struct CampaignMapCell *c=&rows[i];if(c->region<0 || !isfinite(c->centrality) || c->centrality<0 || c->centrality>1 || c->visited>1 || c->valid>1){free(rows);return -3;}
        if(!c->region)continue;int found=0;for(int j=0;j<region_count;j++)if(regions[j].ident==c->region){found=1;break;}
        if(found)continue;if(region_count>=1024){free(rows);return -3;}
        uintptr_t region=campaign_region(meta,c->region);struct CampaignMapRegion *r=&regions[region_count];
        if(!region || !read_mem((void*)region,r,12)){free(rows);return -4;}region_count++;
    }
    uintptr_t save,objectives[3];
    if(!read_mem((void*)(meta+0x40),&save,8) || !save || !read_mem((void*)(save+0xe0),objectives,24) ||
       objectives[1]<objectives[0] || objectives[2]<objectives[1] || (objectives[1]-objectives[0])%8 || objectives[2]-objectives[0]>8192*8){free(rows);return -6;}
    FILE *out=fopen(path,"wb");if(!out){free(rows);return -5;}
    fprintf(out,"{\"version\":1,\"exploration\":\"%s\",\"radius\":[%.9g,%.9g],\"width\":%d,\"cells\":[",campaign_shared_exploration?"shared":"independent",radius[0],radius[1],width);
    for(int i=0;i<width*width;i++){struct CampaignMapCell *c=&rows[i];fprintf(out,"%s[%d,%.9g,%u,%u]",i?",":"",c->region,c->centrality,c->visited,c->valid);}
    fprintf(out,"],\"regions\":[");for(int i=0;i<region_count;i++)fprintf(out,"%s[%d,%u,%d]",i?",":"",regions[i].ident,regions[i].color,regions[i].faction);
    fprintf(out,"],\"objectives\":[");int emitted=0;
    typedef void *(*Position)(void*,void*);Position position=(Position)((char*)GetModuleHandleW(NULL)+0x17c8b0);
    const unsigned char pos_sig[]={0x48,0x89,0x5c,0x24,0x10,0x57,0x48,0x83,0xec,0x20};
    if(memcmp((void*)position,pos_sig,sizeof(pos_sig))){fclose(out);free(rows);return -6;}
    for(uintptr_t i=objectives[0];i<objectives[1];i+=8){
        uintptr_t obj;struct CampaignMapObjective o;memset(&o,0,sizeof(o));
        if(!read_mem((void*)i,&obj,8) || !obj || !read_mem((void*)(obj+0x20),&o.dx,12) ||
           !read_mem((void*)(obj+0x2c),&o.flags,4) || !read_mem((void*)(obj+0x30),&o.ident,4) || !read_mem((void*)(obj+0x34),&o.faction,4)){fclose(out);free(rows);return -6;}
        position((void*)obj,&o.x);o.key=campaign_objective_key(obj);
        if(!o.key || !isfinite(o.x) || !isfinite(o.y) || !isfinite(o.dx) || !isfinite(o.dy) || !isfinite(o.radius) || o.radius<0){fclose(out);free(rows);return -6;}
        fprintf(out,"%s[%u,%u,%d,%u,%.9g,%.9g,%.9g,%.9g,%.9g]",emitted++?",":"",o.key,o.ident,o.faction,o.flags,o.x,o.y,o.dx,o.dy,o.radius);
    }
    fprintf(out,"]}");int failed=ferror(out);fclose(out);free(rows);return failed?-5:width*width;
}
__declspec(dllexport) int RepopulatedExportCampaignMap(void *zone,const char *path){
    initialize();return export_campaign_map(zone,path,NULL);
}
__declspec(dllexport) int RepopulatedExportRemoteCampaignMap(void *zone,const char *path){
    initialize();if(!remote_campaign_map(zone))return -1;
    return export_campaign_map(zone,path,campaign_shared_exploration?NULL:remote_map_cells);
}
__declspec(dllexport) int RepopulatedApplyCampaignObjectives(void *zone,const struct CampaignMapObjective *rows,int count){
    initialize();int width;float radius[2];uintptr_t cells,meta=campaign_map(zone,&width,radius,&cells),save,vector[3];
    HMODULE game=GetModuleHandleW(NULL);
    const unsigned char push_sig[]={0x40,0x53,0x57,0x41,0x57,0x48,0x83,0xec,0x20};
    const unsigned char alloc_sig[]={0x40,0x53,0x48,0x83,0xec,0x20,0x48,0x8b,0xd9};
    const unsigned char render_sig[]={0x48,0x89,0x5c,0x24,0x10,0x57,0x48,0x83,0xec,0x20};
    if(!meta || !rows || count<0 || count>8192 || !read_mem((void*)(meta+0x40),&save,8) || !save ||
       !read_mem((void*)(save+0xe0),vector,24) || vector[1]<vector[0] || vector[2]<vector[1] || (vector[1]-vector[0])%8 || vector[2]-vector[0]>8192*8 ||
       memcmp((char*)game+0x5f700,push_sig,sizeof(push_sig)) || memcmp((char*)game+0x2f5588,alloc_sig,sizeof(alloc_sig)) ||
       memcmp((char*)game+0x1ddb70,render_sig,sizeof(render_sig)))return -1;
    uintptr_t targets[8192];int new_count=0;
    for(int i=0;i<count;i++){
        const struct CampaignMapObjective *o=&rows[i];targets[i]=0;
        if(!o->key || o->faction<0 || o->faction>1000000 || !isfinite(o->x) || !isfinite(o->y) || fabsf(o->x)>1e6 || fabsf(o->y)>1e6 ||
           !isfinite(o->dx) || !isfinite(o->dy) || fabsf(o->dx)>1e6 || fabsf(o->dy)>1e6 || !isfinite(o->radius) || o->radius<0 || o->radius>1e6)return -2;
        for(int j=0;j<i;j++)if(rows[j].key==o->key || (o->ident && rows[j].ident==o->ident))return -2;
        for(unsigned j=0;j<campaign_objective_count;j++)if(campaign_objectives[j].key==o->key){targets[i]=campaign_objectives[j].pointer;break;}
        /* Replica objectives are independent map objects. Linking them to a
           locally extrapolated block would overwrite the authoritative marker
           position during getWrappedPos(), especially while opening a menu. */
        if(!targets[i])new_count++;
    }
    if(campaign_objective_count+new_count>8192)return -4;
    typedef void *(*Allocate)(size_t);Allocate allocate=(Allocate)((char*)game+0x2f5588);
    typedef void (*Push)(void*,uintptr_t*);Push push=(Push)((char*)game+0x5f700);
    typedef void (*Render)(void*);Render render=(Render)((char*)game+0x1ddb70);
    /* Exact native CRT imports used by SaveGame::onZoneRender (0x1ddb70).
       Serialize changes with its recursive save mutex and the render copy. */
    typedef int (*Mutex)(void*);Mutex lock,unlock;
    memcpy(&lock,(char*)game+0x3162c0,8);memcpy(&unlock,(char*)game+0x3162d0,8);
    if(!lock || !unlock || lock((void*)(save+0x1f0)))return -5;
    for(int i=0;i<count;i++){
        if(!targets[i]){targets[i]=(uintptr_t)allocate(0x60);memset((void*)targets[i],0,0x60);uintptr_t vt=(uintptr_t)game+0x339590;memcpy((void*)targets[i],&vt,8);}
        int found=0;for(unsigned j=0;j<campaign_objective_count;j++)if(campaign_objectives[j].key==rows[i].key){found=1;break;}
        if(!found)campaign_objectives[campaign_objective_count++]=(struct CampaignObjectiveRef){targets[i],rows[i].key};
        if(rows[i].key>=campaign_objective_next)campaign_objective_next=rows[i].key+1;
        memcpy((void*)(targets[i]+0x18),&rows[i].x,8);memcpy((void*)(targets[i]+0x20),&rows[i].dx,12);
        memcpy((void*)(targets[i]+0x2c),&rows[i].flags,4);memcpy((void*)(targets[i]+0x30),&rows[i].ident,4);memcpy((void*)(targets[i]+0x34),&rows[i].faction,4);
        memcpy((void*)(targets[i]+0x40),&meta,8);
    }
    /* Retain objective objects for watched selections; only rebuild the native
       pointer vector. Replicas never delete a target while menus reference it. */
    memcpy((void*)(save+0xe8),&vector[0],8);for(int i=0;i<count;i++)push((void*)(save+0xe0),&targets[i]);render((void*)save);
    unlock((void*)(save+0x1f0));
    return count;
}
__declspec(dllexport) int RepopulatedApplyCampaignMap(void *zone,const float *radius,int width,
    const struct CampaignMapCell *rows,int count,const struct CampaignMapRegion *regions,int region_count) {
    initialize();int local_width;float local_radius[2];uintptr_t cells,meta=campaign_map(zone,&local_width,local_radius,&cells);
    if(!meta || !radius || !rows || !regions || width!=local_width || count!=width*width || region_count<0 || region_count>1024 ||
       radius[0]!=local_radius[0] || radius[1]!=local_radius[1])return -1;
    uintptr_t targets[1024];
    /* Validate the entire transaction before writing any native field. */
    for(int i=0;i<region_count;i++){
        if(regions[i].ident<=0 || regions[i].faction<0 || regions[i].faction>1000000)return -2;
        for(int j=0;j<i;j++)if(regions[j].ident==regions[i].ident)return -2;
        targets[i]=campaign_region(meta,regions[i].ident);if(!targets[i])return -4;
    }
    for(int i=0;i<count;i++){
        const struct CampaignMapCell *c=&rows[i];if(c->region<0 || !isfinite(c->centrality) || c->centrality<0 || c->centrality>1 || c->visited>1 || c->valid>1)return -3;
        if(c->region){int found=0;for(int j=0;j<region_count;j++)if(regions[j].ident==c->region){found=1;break;}if(!found)return -4;}
    }
    int changed=0;for(int i=0;i<count;i++){
        /* Save validity describes files belonging to this process, not visibility. */
        if(memcmp((void*)(cells+i*12),&rows[i],9)){memcpy((void*)(cells+i*12),&rows[i],9);changed=1;}
    }
    for(int i=0;i<region_count;i++)if(memcmp((void*)(targets[i]+4),&regions[i].color,8)){
        memcpy((void*)(targets[i]+4),&regions[i].color,8);changed=1;
    }
    if(changed){unsigned version;memcpy(&version,(void*)(meta+0x38),4);version++;memcpy((void*)(meta+0x38),&version,4);}
    return count;
}
