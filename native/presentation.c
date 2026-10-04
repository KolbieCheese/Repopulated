/* Exact-build presentation replication. No client physics, weapon simulation,
 * resources, or health changes. Included after world_snapshot.c. */
struct VisualBlock {
    unsigned ident,block_ident,type; float x,y,angle; unsigned mask;
    float turret,laser[5]; unsigned hitting;
};
struct VisualProjectile { float x,y,vx,vy,angle,size,ttl; unsigned color; float health; };
struct VisualThrust { float x,y,vx,vy,size; unsigned color; float vx2,vy2,size2; unsigned color2; float delay; };
_Static_assert(sizeof(struct VisualBlock)==56,"wire block layout");
_Static_assert(sizeof(struct VisualProjectile)==36,"wire projectile layout");
_Static_assert(sizeof(struct VisualThrust)==44,"wire thrust layout");
typedef void (*NativeThrust)(void*,uint64_t,uint64_t,float,unsigned,uint64_t,float,unsigned);
static NativeThrust original_thrust;
static SRWLOCK thrust_lock=SRWLOCK_INIT;
static struct VisualThrust captured_thrust[4096];
static ULONGLONG captured_ticks[4096];
static unsigned captured_count,captured_dropped;
static struct VisualProjectile replica_projectiles[2048];
static struct VisualBlock replica_blocks[4096];
static int replica_block_count;
static struct VisualThrust replica_thrust[512];
static int replica_projectile_count,replica_thrust_count,replica_thrust_next;
static void *presentation_zone;
static ULONGLONG presentation_tick;
static unsigned long long replayed_thrust,drawn_projectiles,applied_turrets,applied_lasers,rendered_beams;
static unsigned long long beam_zones,beam_components,beam_matches,beam_active_sources;
static char presentation_message[256];
static unsigned next_visual_block=0x78000000;
__declspec(dllexport) const char *RepopulatedPresentationMessage(void) { return presentation_message; }
__declspec(dllexport) int RepopulatedEnsureVisualIdentities(void *zone) {
    initialize();if(!compatible || !zone)return -1;uintptr_t roots[2];
    if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || (roots[1]-roots[0])%8 || roots[1]-roots[0]>4096*8)return -2;
    /* Preserve saved identifiers and place new ones beyond their high-water
     * mark. A visual block can change its center-relative position on load. */
    int assigned=0;
    for(int pass=0;pass<2;pass++)for(size_t i=0;i<(roots[1]-roots[0])/8;i++) {
        uintptr_t cluster,blocks[2];if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0xf0),blocks,16) ||
            blocks[1]<blocks[0] || (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>4096*8)return -3;
        for(size_t j=0;j<(blocks[1]-blocks[0])/8;j++) {
            uintptr_t block;unsigned ident;uint64_t features;
            if(!read_mem((void*)(blocks[0]+j*8),&block,8) || !read_mem((void*)(block+0x30),&ident,4) || !read_mem((void*)(block+0x40),&features,8))return -3;
            if(!pass){if(ident>=next_visual_block && ident<0x7fffffff)next_visual_block=ident+1;continue;}
            /* Stable IDs on every block let replica health updates preserve
             * native objects, including unarmed hull and command blocks. */
            if(ident)continue;
            if(next_visual_block>=0x7fffffff)return -4;
            memcpy((void*)(block+0x30),&next_visual_block,4);next_visual_block++;assigned++;
        }
    }
    return assigned;
}
static bool presentation_functions(NativeThrust *thrust) {
    HMODULE game=GetModuleHandleW(NULL);
    unsigned char actual[12];
    const unsigned char effect_sig[]={0x48,0x8b,0xc4,0x48,0x89,0x58,0x08,0x55,0x48,0x8d,0x68,0xc1};
    const unsigned char projectile_sig[]={0x40,0x53,0x56,0x57,0x48,0x83,0xec,0x20,0x48,0x8b,0x79,0x08};
    const unsigned char turret_sig[]={0xf6,0x41,0x40,0x10,0x74,0x11,0x48,0x8b,0x81,0x58,0x01,0x00};
    uintptr_t address=(uintptr_t)game+0x1cc440;
    /* The host's replaceFast hook modifies the effect entry point. The
     * original signature is checked by JS before installing that hook. */
    if(!original_thrust && (!read_mem((void*)address,actual,12) || memcmp(actual,effect_sig,12))) return false;
    if(!read_mem((char*)game+0x171400,actual,12) || memcmp(actual,projectile_sig,12) ||
       !read_mem((char*)game+0x29d7f0,actual,12) || memcmp(actual,turret_sig,12)) return false;
    memcpy(thrust,&address,sizeof(address));return true;
}
__declspec(dllexport) void RepopulatedSetThrustOriginal(void *address) { memcpy(&original_thrust,&address,sizeof(address)); }
__declspec(dllexport) void RepopulatedCaptureThrust(void *system,uint64_t pos,uint64_t vel,float size,unsigned color,uint64_t vel2,float size2,unsigned color2) {
    union {uint64_t packed;float xy[2];} p={.packed=pos},v={.packed=vel},v2={.packed=vel2};
    AcquireSRWLockExclusive(&thrust_lock);
    if(captured_count<4096) {
        captured_thrust[captured_count]=(struct VisualThrust){p.xy[0],p.xy[1],v.xy[0],v.xy[1],size,color,v2.xy[0],v2.xy[1],size2,color2,0};
        captured_ticks[captured_count++]=GetTickCount64();
    } else captured_dropped++;
    ReleaseSRWLockExclusive(&thrust_lock);
    if(original_thrust) original_thrust(system,pos,vel,size,color,vel2,size2,color2);
}
static bool near_focus(double x,double y,const double *focus,float radius) {
    double dx=x-focus[0],dy=y-focus[1];return isfinite(x)&&isfinite(y)&&dx*dx+dy*dy<=(double)radius*radius;
}
__declspec(dllexport) int RepopulatedExportPresentation(void *zone,const char *path,unsigned focus,float radius) {
    initialize();NativeThrust unused;
    if(!compatible || !zone || !path || !focus || !isfinite(radius) || radius<100 || radius>10000 || !presentation_functions(&unused)) return -1;
    uintptr_t vector[2];double center[2];bool found=false;
    if(!read_mem((char*)zone+0x188,vector,16) || vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8) return -2;
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster;if(!read_mem((void*)(vector[0]+i*8),&cluster,8))return -2;
        if(RepopulatedClusterIdent((void*)cluster)==focus) {
            if(found || !read_mem((void*)(cluster+0x30),center,16))return -3;found=true;
        }
    }
    if(!found)return -4;
    FILE *file=fopen(path,"wb");if(!file)return -5;
    int block_count=0,projectile_count=0,thrust_count=0,error=0;
    fputs("{\"version\":2,\"blocks\":[",file);
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent,blocks[2];double position[2];
        if(!read_mem((void*)(vector[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8)) {error=-6;break;}
        if(parent)continue;
        if(!read_mem((void*)(cluster+0x30),position,16) || !near_focus(position[0],position[1],center,radius))continue;
        unsigned ident=RepopulatedClusterIdent((void*)cluster);
        if(!ident || !read_mem((void*)(cluster+0xf0),blocks,16) || blocks[1]<blocks[0] || (blocks[1]-blocks[0])%8 || blocks[1]-blocks[0]>4096*8){error=-6;break;}
        for(size_t j=0;j<(blocks[1]-blocks[0])/8;j++) {
            uintptr_t block,turret,laser;uint64_t features;struct VisualBlock record={.ident=ident};
            if(!read_mem((void*)(blocks[0]+j*8),&block,8) || !read_mem((void*)(block+0x40),&features,8)){error=-6;break;}
            record.mask=((features&0x10)?1:0)|((features&0x80)?2:0);if(!record.mask)continue;
            if(block_count>=4096 || !read_mem((void*)(block+0x18),&record.type,4) || !read_mem((void*)(block+0x30),&record.block_ident,4) ||
               !record.block_ident || !read_mem((void*)(block+0x1c),&record.x,12)){
                snprintf(presentation_message,sizeof(presentation_message),"Export missing visual ID entity=%u type=%u blockId=%u count=%d",ident,record.type,record.block_ident,block_count);error=-7;break;
            }
            if(record.mask&1) {
                if(!read_mem((void*)(block+0x158),&turret,8) || !turret || !read_mem((void*)turret,&record.turret,4)){error=-6;break;}
            }
            if(record.mask&2) {
                unsigned char hitting;
                if(!read_mem((void*)(block+0x150),&laser,8) || !laser || !read_mem((void*)laser,record.laser,4) ||
                   !read_mem((void*)(laser+0x24),record.laser+1,8) || !read_mem((void*)(laser+0x3c),record.laser+3,8) ||
                   !read_mem((void*)(laser+0x45),&hitting,1)){error=-6;break;}
                record.hitting=hitting?1:0;
            }
            if(block_count++)fputc(',',file);
            fprintf(file,"[%u,%u,%u,%.9g,%.9g,%.9g,%u,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%u]",
                record.ident,record.block_ident,record.type,record.x,record.y,record.angle,record.mask,record.turret,
                record.laser[0],record.laser[1],record.laser[2],record.laser[3],record.laser[4],record.hitting);
        }
        if(error)break;
    }
    fputs("],\"motion\":[",file);
    int motion_count=0;
    for(size_t i=0;!error && i<(vector[1]-vector[0])/8;i++) {
        uintptr_t cluster,parent;double position[2];float angular;
        if(!read_mem((void*)(vector[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8)){error=-6;break;}
        if(parent)continue;
        if(!read_mem((void*)(cluster+0x30),position,16) || !near_focus(position[0],position[1],center,radius))continue;
        /* Body::getAngVel RVA 0x5cbd0 reads this+0x64 in the verified build. */
        if(!read_mem((void*)(cluster+0x64),&angular,4) || !isfinite(angular) || fabsf(angular)>10000){error=-6;break;}
        if(motion_count++)fputc(',',file);
        fprintf(file,"[%u,%.9g]",RepopulatedClusterIdent((void*)cluster),angular);
    }
    fputs("],\"projectiles\":[",file);
    uintptr_t shots[2];float simtime;
    if(!read_mem((char*)zone+0x208,shots,16) || shots[1]<shots[0] || (shots[1]-shots[0])%8 || shots[1]-shots[0]>1000000*8 ||
       !read_mem((char*)zone+0x158,&simtime,4) || !isfinite(simtime))error=-8;
    if(!error)for(size_t i=0;i<(shots[1]-shots[0])/8;i++) {
        uintptr_t shot;double pose[4];struct VisualProjectile record;float end;
        if(!read_mem((void*)(shots[0]+i*8),&shot,8) || !read_mem((void*)(shot+0x30),pose,32)){error=-8;break;}
        if(!near_focus(pose[0],pose[1],center,radius))continue;
        if(projectile_count>=2048)break;
        if(!read_mem((void*)(shot+0x60),&record.angle,4) || !read_mem((void*)(shot+0x1b8),&record.size,4) ||
           !read_mem((void*)(shot+0x1bc),&end,4) || !read_mem((void*)(shot+0x1c0),&record.health,4) ||
           !read_mem((void*)(shot+0x1c4),&record.color,4)){error=-8;break;}
        record.ttl=end-simtime;if(record.ttl<=0)continue;
        if(projectile_count++)fputc(',',file);
        fprintf(file,"[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%u,%.9g]",pose[0],pose[1],pose[2],pose[3],record.angle,record.size,record.ttl,record.color,record.health);
    }
    fputs("],\"thrust\":[",file);
    ULONGLONG now=GetTickCount64(),first=now;
    AcquireSRWLockExclusive(&thrust_lock);
    for(unsigned i=0;i<captured_count;i++)if(now-captured_ticks[i]<=300 && near_focus(captured_thrust[i].x,captured_thrust[i].y,center,radius)) {first=captured_ticks[i];break;}
    unsigned eligible=0,visited=0,selected=0;
    for(unsigned i=0;i<captured_count;i++)if(now-captured_ticks[i]<=300 && near_focus(captured_thrust[i].x,captured_thrust[i].y,center,radius))eligible++;
    for(unsigned i=0;i<captured_count;i++) {
        struct VisualThrust *r=captured_thrust+i;
        if(now-captured_ticks[i]>300 || !near_focus(r->x,r->y,center,radius))continue;
        /* Spread a bounded particle budget over the entire frame window.
         * Keeping only the earliest 512 produced a visible exhaust pulse. */
        unsigned index=visited++;
        if(eligible>512 && index!=(unsigned)((uint64_t)selected*(eligible-1)/511))continue;
        selected++;
        if(thrust_count++)fputc(',',file);
        fprintf(file,"[%.9g,%.9g,%.9g,%.9g,%.9g,%u,%.9g,%.9g,%.9g,%u,%.9g]",r->x,r->y,r->vx,r->vy,r->size,r->color,r->vx2,r->vy2,r->size2,r->color2,(double)(captured_ticks[i]-first)/1000);
    }
    fprintf(file,"],\"droppedThrust\":%u}",captured_dropped+(eligible>512?eligible-512:0));captured_count=0;captured_dropped=0;
    ReleaseSRWLockExclusive(&thrust_lock);
    if(fclose(file))return -9;return error?error:block_count+projectile_count+thrust_count;
}
__declspec(dllexport) int RepopulatedApplyPresentation(void *zone,const struct VisualBlock *records,int count,const struct VisualProjectile *shots,int shot_count,const struct VisualThrust *thrust,int thrust_count) {
    initialize();NativeThrust unused;
    if(!compatible || !zone || count<0 || count>4096 || shot_count<0 || shot_count>2048 || thrust_count<0 || thrust_count>512 || !presentation_functions(&unused))return -1;
    uintptr_t roots[2];if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || roots[1]-roots[0]>4096*8)return -2;
    int applied=0;
    for(size_t i=0;i<(roots[1]-roots[0])/8;i++) {
        uintptr_t cluster,parent,blocks[2];if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8))return -3;
        if(parent)continue;unsigned ident=RepopulatedClusterIdent((void*)cluster);
        if(!read_mem((void*)(cluster+0xf0),blocks,16) || blocks[1]<blocks[0] || blocks[1]-blocks[0]>4096*8)return -3;
        for(int r=0;r<count;r++) {
            if(records[r].ident!=ident)continue;uintptr_t match=0;
            for(size_t j=0;j<(blocks[1]-blocks[0])/8;j++) {
                uintptr_t block;unsigned type,block_ident;
                if(!read_mem((void*)(blocks[0]+j*8),&block,8) || !read_mem((void*)(block+0x18),&type,4) || !read_mem((void*)(block+0x30),&block_ident,4))return -3;
                if(type==records[r].type && block_ident==records[r].block_ident){if(match)return -4;match=block;}
            }
            if(!match) {
                snprintf(presentation_message,sizeof(presentation_message),"Missing visual block entity=%u type=%u xy=(%.9g,%.9g) angle=%.9g",ident,records[r].type,records[r].x,records[r].y,records[r].angle);
                return -5;
            }
            uintptr_t pointer;uint64_t features;if(!read_mem((void*)(match+0x40),&features,8))return -3;
            if(records[r].mask&1) {
                if(!(features&0x10) || !read_mem((void*)(match+0x158),&pointer,8) || !pointer)return -6;
                /* Turret.angle and RenderAngle's previous/current/render slots. */
                for(int k=0;k<4;k++)memcpy((void*)(pointer+k*4),&records[r].turret,4);applied_turrets++;
            }
            if(records[r].mask&2) {
                if(!(features&0x80) || !read_mem((void*)(match+0x150),&pointer,8) || !pointer)return -6;
                float p[2];if(!read_mem((void*)(match+0x1c),p,8))return -3;
                float start[2]={records[r].laser[1]+p[0]-records[r].x,records[r].laser[2]+p[1]-records[r].y};
                float end[2]={records[r].laser[3]+p[0]-records[r].x,records[r].laser[4]+p[1]-records[r].y};
                memcpy((void*)pointer,records[r].laser,4);
                memcpy((void*)(pointer+4),start,8);memcpy((void*)(pointer+0xc),end,8);
                for(int k=0;k<3;k++){memcpy((void*)(pointer+0x14+k*8),start,8);memcpy((void*)(pointer+0x2c+k*8),end,8);}
                *(unsigned char*)(pointer+0x45)=records[r].hitting?1:0;
                *(unsigned char*)(match+0xf0)|=2;applied_lasers++;
            }
            applied++;
        }
    }
    if(applied!=count)return -7;
    memcpy(replica_blocks,records,(size_t)count*sizeof(*records));replica_block_count=count;
    memcpy(replica_projectiles,shots,(size_t)shot_count*sizeof(*shots));replica_projectile_count=shot_count;
    memcpy(replica_thrust,thrust,(size_t)thrust_count*sizeof(*thrust));replica_thrust_count=thrust_count;replica_thrust_next=0;
    presentation_zone=zone;presentation_tick=GetTickCount64();return applied;
}
__declspec(dllexport) int RepopulatedTickPresentation(void *zone) {
    if(zone!=presentation_zone)return 0;
    float elapsed=(float)(GetTickCount64()-presentation_tick)/1000;
    if(elapsed>0.5f){replica_thrust_next=replica_thrust_count;return 0;}
    NativeThrust emit;if(!presentation_functions(&emit))return -1;
    void *system;if(!read_mem(zone,&system,sizeof(system)) || !system)return -2;
    int count=0;
    while(replica_thrust_next<replica_thrust_count && replica_thrust[replica_thrust_next].delay<=elapsed) {
        struct VisualThrust *r=replica_thrust+replica_thrust_next++;
        union{float xy[2];uint64_t packed;} p={.xy={r->x+world_visual_center[0],r->y+world_visual_center[1]}},v={.xy={r->vx,r->vy}},v2={.xy={r->vx2,r->vy2}};
        emit(system,p.packed,v.packed,r->size,r->color,v2.packed,r->size2,r->color2);count++;replayed_thrust++;
    }
    return count;
}
__declspec(dllexport) int RepopulatedDrawReplicaProjectiles(void *zone) {
    if(zone!=presentation_zone)return 0;
    float elapsed=(float)(GetTickCount64()-presentation_tick)/1000;if(elapsed>0.3f)return 0;
    typedef void (*Append)(void*,const void*);Append append;uintptr_t address=(uintptr_t)GetModuleHandleW(NULL)+0x171400;memcpy(&append,&address,8);
    int count=0;
    for(int i=0;i<replica_projectile_count;i++) {
        struct VisualProjectile *p=replica_projectiles+i;if(elapsed>=p->ttl)continue;
        struct {float x,y,angle;unsigned color;float alpha,size,length;} record={p->x+p->vx*elapsed+world_visual_center[0],p->y+p->vy*elapsed+world_visual_center[1],p->angle,p->color,
            fmaxf(1.1f,cosf(p->health/50)/4+1),p->size,fminf(p->size*fmaxf(1,hypotf(p->vx,p->vy)/100),100)};
        append((char*)zone+0x330,&record);count++;drawn_projectiles++;
    }
    return count;
}
__declspec(dllexport) int RepopulatedRenderReplicaBeams(void *zone,void *mesh,const void *view) {
    if(zone!=presentation_zone || GetTickCount64()-presentation_tick>300)return 0;
    beam_zones++;
    HMODULE game=GetModuleHandleW(NULL);
    const char *name="?renderEffect@Block@@QEBAXAEAU?$TriMesh@UVertexPosColorLuma@@@@AEBUView@@@Z";
    const unsigned char prefix[]={0x48,0x8b,0xc4,0x48,0x89,0x58,0x10};
    if(!signature(game,name,prefix,sizeof(prefix)))return -1;
    typedef void (*Render)(const void*,void*,const void*);Render render;FARPROC raw=GetProcAddress(game,name);memcpy(&render,&raw,8);
    uintptr_t roots[2];if(!read_mem((char*)zone+0x188,roots,16) || roots[1]<roots[0] || roots[1]-roots[0]>4096*8)return -2;
    int count=0;
    for(size_t i=0;i<(roots[1]-roots[0])/8;i++) {
        uintptr_t cluster,blocks[2];if(!read_mem((void*)(roots[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0xf0),blocks,16) || blocks[1]<blocks[0] || blocks[1]-blocks[0]>4096*8)return -3;
        for(size_t j=0;j<(blocks[1]-blocks[0])/8;j++) {
            uintptr_t block,laser;uint64_t features;float firing;
            if(!read_mem((void*)(blocks[0]+j*8),&block,8) || !read_mem((void*)(block+0x40),&features,8))return -3;
            if(!(features&0x80))continue;
            if(!read_mem((void*)(block+0x150),&laser,8))return -3;
            if(!laser)continue; /* Native renderEffect also skips absent components. */
            beam_components++;
            unsigned block_ident,ident=RepopulatedClusterIdent((void*)cluster);
            if(!read_mem((void*)(block+0x30),&block_ident,4))return -3;
            /* advanceUpdate resets laser firing each local step even when
             * Block::update is suppressed. Restore only renderer fields at
             * draw time, resolving current blocks by IDs after every load. */
            for(int r=0;r<replica_block_count;r++) {
                struct VisualBlock *record=replica_blocks+r;
                if(record->ident!=ident || record->block_ident!=block_ident || !(record->mask&2))continue;
                beam_matches++;if(record->laser[0]>0)beam_active_sources++;
                float p[2];if(!read_mem((void*)(block+0x1c),p,8))return -3;
                float start[2]={record->laser[1]+p[0]-record->x,record->laser[2]+p[1]-record->y};
                float end[2]={record->laser[3]+p[0]-record->x,record->laser[4]+p[1]-record->y};
                memcpy((void*)laser,record->laser,4);
                for(int k=0;k<3;k++){memcpy((void*)(laser+0x14+k*8),start,8);memcpy((void*)(laser+0x2c+k*8),end,8);}
                *(unsigned char*)(laser+0x45)=record->hitting?1:0;break;
            }
            if(!read_mem((void*)laser,&firing,4))return -3;
            if(firing<=0)continue;
            render((void*)block,mesh,view);count++;rendered_beams++;
        }
    }
    return count;
}
__declspec(dllexport) void RepopulatedPresentationStats(unsigned long long *out) {
    out[0]=replayed_thrust;out[1]=drawn_projectiles;out[2]=applied_turrets;out[3]=applied_lasers;out[4]=rendered_beams;
    out[5]=beam_zones;out[6]=beam_components;out[7]=beam_matches;out[8]=beam_active_sources;
}
