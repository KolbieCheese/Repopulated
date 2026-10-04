/* Exact-build kinematics. Runs on each process's owning update thread. The
 * client may receive poses before geometry; unknown IDs wait for a scene. */
struct ReplicaKinematics { struct ReplicaPose pose;float angular; };
_Static_assert(sizeof(struct ReplicaKinematics)==44,"kinematics layout");
static char replica_motion_apply_message[640]="No motion application yet";
__declspec(dllexport) const char *RepopulatedMotionApplyMessage(void){return replica_motion_apply_message;}
__declspec(dllexport) int RepopulatedReadMotion(void *zone,unsigned focus,float radius,struct ReplicaKinematics *out,int capacity){
    initialize();if(!compatible || !zone || !out || capacity<1 || capacity>4096 || !isfinite(radius) || radius<1000 || radius>20000)return -1;
    uintptr_t vector[2],pilot=replica_find(zone,focus);double center[2];
    if(!pilot)return 0;
    if(!read_mem((void*)(pilot+0x30),center,16) || !read_mem((char*)zone+0x188,vector,16) || vector[1]<vector[0] || (vector[1]-vector[0])%8 || vector[1]-vector[0]>4096*8)return -2;
    int count=0;double sampled=replica_now_millis();
    for(size_t i=0;i<(vector[1]-vector[0])/8;i++){
        uintptr_t cluster,parent;double state[4];int faction;
        if(!read_mem((void*)(vector[0]+i*8),&cluster,8) || !read_mem((void*)(cluster+0x178),&parent,8))return -2;
        if(parent)continue;
        if(!read_mem((void*)(cluster+0x30),state,32) || !read_mem((void*)(cluster+0x118),&faction,4))return -2;
        double dx=state[0]-center[0],dy=state[1]-center[1];if(dx*dx+dy*dy>(double)radius*radius)continue;
        unsigned ident=RepopulatedClusterIdent((void*)cluster);if(!ident)continue;
        if(ident==0x70000001 || ident==0x70000002){
            replica_motion_trace_pointer[ident-0x70000001]=cluster;replica_motion_trace_observed[ident-0x70000001]=sampled;
        }
        if(count==capacity)return -3;
        /* A commandless fragment can keep an inherited native faction cache
         * that the serializer does not carry. Use the geometry's ownership
         * definition without modifying authoritative native allegiance. */
        int network_faction=replica_commandless_owner();
        uintptr_t command=owned_cluster_command((void*)cluster),serial=0;
        if(command && (!read_mem((void*)(command+0x28),&serial,8) || !replica_serial_owner(serial,ident,&network_faction)))return -2;
        if(faction!=network_faction)InterlockedIncrement64(&replica_ownership_counts[command?2:0]);
        struct ReplicaKinematics row={.pose={.ident=ident,.faction=(unsigned)network_faction,.x=(float)state[0],.y=(float)state[1],.vx=(float)state[2],.vy=(float)state[3]}};
        if(!read_mem((void*)(cluster+0x60),&row.pose.angle,4) || !read_mem((void*)(cluster+0x64),&row.angular,4))return -2;
        if(command && serial){
            int energy;if(!read_mem((void*)(serial+0x1c),&energy,4) || !read_mem((void*)(serial+0x14),&row.pose.resources,8))return -2;
            row.pose.energy=(float)(energy<0?0:energy);
        }
        out[count++]=row;
    }
    return count;
}
static int replica_apply_motion(void *zone,const struct ReplicaKinematics *rows,int count){
    initialize();if(!compatible || !zone || !rows || count<1 || count>4096)return -1;
    HMODULE game=GetModuleHandleW(NULL);
    typedef void (*Setter)(void*,uint64_t);typedef void (*Angle)(void*,float);
    Setter position,velocity;Angle rotation,angular;FARPROC raw;
    raw=GetProcAddress(game,"?setPos@BlockCluster@@QEAAXU?$tvec2@M$0A@@glm@@@Z");memcpy(&position,&raw,8);
    raw=GetProcAddress(game,"?setVel@Body@@QEAAXU?$tvec2@M$0A@@glm@@@Z");memcpy(&velocity,&raw,8);
    raw=GetProcAddress(game,"?setAngle@BlockCluster@@QEAAXM@Z");memcpy(&rotation,&raw,8);
    raw=GetProcAddress(game,"?setAngVel@Body@@QEAAXM@Z");memcpy(&angular,&raw,8);
    if(!position || !velocity || !rotation || !angular)return -2;
    for(int i=0;i<count;i++){
        const struct ReplicaPose *p=&rows[i].pose;float v[9];memcpy(v,&p->x,sizeof(v));
        if(!p->ident || p->faction>0x7fffffff)return -3;
        for(int j=0;j<9;j++)if(!isfinite(v[j]) || fabsf(v[j])>(j<2?1e6f:j>=5 && j<=7?1e9f:10000) || (j>=5 && j<=7 && v[j]<0))return -3;
    }
    struct ReplicaRootRef index[4096];int roots_count=replica_root_index(zone,index,NULL);
    if(roots_count<0){snprintf(replica_motion_apply_message,sizeof(replica_motion_apply_message),"seq=%u rows=%d root index rejected (%d): %s",replica_motion_sequence,count,roots_count,replica_root_index_message);return -4;}
    int applied=0;double frame_now=replica_now_millis();
    for(int i=0;i<count;i++){
        const struct ReplicaPose *p=&rows[i].pose;const struct ReplicaRootRef *ref=replica_root_lookup(index,roots_count,p->ident);if(!ref)continue;
        uintptr_t cluster=ref->cluster;
        if((unsigned)ref->faction!=p->faction){snprintf(replica_motion_apply_message,sizeof(replica_motion_apply_message),"seq=%u row=%d id=0x%08x faction mismatch source=%u live=%d nativeCached=%d ownedCommand=%p cluster=%p indexedRoots=%d",replica_motion_sequence,i,p->ident,p->faction,ref->faction,ref->native_faction,(void*)ref->command,(void*)cluster,roots_count);return -4;}
        if(!replica_guard_enter(zone))return -5;
        struct ReplicaSmoothing *history=replica_history(p->ident,true);
        replica_bind_history(history,zone,cluster);
        /* Re-applying the newest packet after geometry can initialize a newly
         * loaded root, but must not reconcile a retained root a second time. */
        struct ReplicaMotionDecision decision=replica_replacement_motion_decision(history,replica_motion_sequence);
        if(decision.duplicate)replica_motion_duplicates++;
        union{float xy[2];uint64_t packed;}pos={.xy={p->x+world_visual_center[0],p->y+world_visual_center[1]}},vel={.xy={p->vx,p->vy}};
        float angle=p->angle,spin=rows[i].angular;
        if(decision.accept){
            prediction_reconcile(p->ident,pos.xy,vel.xy,&angle,&spin);
            if(replica_is_predicted(p->ident))replica_correction(p->ident,pos.xy[0],pos.xy[1],angle);
            else {
                double now=frame_now;
                double rate=replica_motion_sequence?replica_simulation_clock.rate:1;
                struct ReplicaTimelinePose pose={pos.xy[0],pos.xy[1],vel.xy[0]*rate,vel.xy[1]*rate,angle,spin*rate};
                replica_timeline_accept(&history->timeline,pose,replica_motion_sequence?replica_motion_sampled_at:now,now);
                /* Keep native SIM velocities beside their captured wall/SIM
                 * rate. Rendering uses the same immutable wall tangents;
                 * local particles interpolate native endpoint velocities. */
                double source=replica_motion_sequence?replica_motion_source_at:now-(replica_motion_clock_ready?replica_motion_clock_offset:0);
                struct ReplicaTimelinePose native_pose={pos.xy[0],pos.xy[1],vel.xy[0],vel.xy[1],angle,spin};
                if(replica_interpolation_accept_sim(&history->interpolation,native_pose,source,rate)){history->presented=false;replica_interpolation_stats[4]++;}
                replica_max_correction=fmax(replica_max_correction,hypot(history->timeline.dx,history->timeline.dy));
                replica_max_angle_correction=fmax(replica_max_angle_correction,fabs(history->timeline.da));
                history->received=now;
            }
            history->motion_sequence=replica_motion_sequence;history->motion_received=frame_now;
            replica_emitter_publish_history(history);
        }
        if(decision.initialize){
            /* A verified replacement keeps the same source sequence, so its
             * replay is duplicate timeline input but new native body input. */
            rotation((void*)cluster,angle);position((void*)cluster,pos.packed);velocity((void*)cluster,vel.packed);angular((void*)cluster,spin);
            /* Native setPos rewrites all render samples to the raw pose. The
             * draw/camera callback can skip while this writer owns the cache,
             * so restore the evaluated presentation before releasing it. */
            if(history->timeline.valid && !replica_is_predicted(p->ident)){
                ReplicaRenderPose render=replica_render_setter();if(!render){replica_guard_leave(zone);return -2;}
                double target=frame_now-replica_presentation_delay;
                double source_target=replica_source_view_time(frame_now,replica_motion_clock_ready?replica_motion_clock_offset:0,replica_presentation_delay);
                struct ReplicaTimelinePose shown=replica_presentation_delay>0 && history->interpolation.count?
                    replica_interpolation_evaluate(&history->interpolation,source_target):replica_timeline_evaluate(&history->timeline,target);
                /* Keep the prepared display sample while simulation mutates
                 * the raw body. The next camera/draw pass advances rendering. */
                if(history->presented){shown.x=history->last_x;shown.y=history->last_y;shown.angle=history->last_angle;}
                union{float xy[2];uint64_t packed;} visible={.xy={(float)shown.x,(float)shown.y}};
                render((void*)cluster,visible.packed,(float)shown.angle);
                history->last_x=visible.xy[0];history->last_y=visible.xy[1];history->last_angle=(float)shown.angle;history->presented=true;
                InterlockedIncrement64(&replica_presentation_guard_stats[2]);
            }
            replica_replacement_initialized(history);
        }
        replica_guard_leave(zone);
        applied++;
        /* Energy/resources are atomic scalar writes on the owner update
         * thread and do not alter our timeline or the root's lifetime. */
        uintptr_t command=owned_cluster_command((void*)cluster),serial;
        if(command && read_mem((void*)(command+0x28),&serial,8) && serial){
            int energy=(int)p->energy;memcpy((void*)(serial+0x1c),&energy,4);memcpy((void*)(serial+0x14),&p->resources,8);
            uintptr_t metadata;if(read_mem((void*)(cluster+0x110),&metadata,8) && metadata)memcpy((void*)(metadata+0x1c),&p->energy,4);
        }
    }
    snprintf(replica_motion_apply_message,sizeof(replica_motion_apply_message),"seq=%u applied=%d/%d indexedRoots=%d",replica_motion_sequence,applied,count,roots_count);
    return applied;
}
__declspec(dllexport) int RepopulatedApplyMotionFrame(void *zone,const struct ReplicaKinematics *rows,int count){
    initialize();if(!compatible || !zone)return -5;
    return replica_apply_motion(zone,rows,count);
}
