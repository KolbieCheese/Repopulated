/* Immutable cosmetic input. Reader counts prevent bank reuse during a copy;
 * publication never waits and never holds an engine/presentation-cache lock. */
#ifndef REPOPULATED_EMITTER_CURVE_H
#define REPOPULATED_EMITTER_CURVE_H
struct ReplicaEmitterCurve {
    unsigned ident;uintptr_t root;
    double clock_offset,delay,source_rate;
    struct ReplicaTimeline timeline;
    struct ReplicaInterpolationBuffer interpolation;
};
struct ReplicaEmitterCurveBank {volatile LONG readers;struct ReplicaEmitterCurve curve;};
struct ReplicaEmitterPublication {volatile LONG key,current;struct ReplicaEmitterCurveBank banks[3];};
static bool replica_emitter_publish(struct ReplicaEmitterPublication *p,const struct ReplicaEmitterCurve *curve){
    LONG current=InterlockedCompareExchange(&p->current,0,0);
    for(int n=1;n<=2;n++){
        LONG next=(current+n)%3;struct ReplicaEmitterCurveBank *bank=&p->banks[next];
        if(InterlockedCompareExchange(&bank->readers,0,0))continue;
        bank->curve=*curve;
        InterlockedExchange(&p->current,next);InterlockedExchange(&p->key,(LONG)curve->ident);
        return true;
    }
    return false;
}
static bool replica_emitter_copy(struct ReplicaEmitterPublication *p,unsigned ident,uintptr_t root,struct ReplicaEmitterCurve *out){
    if(!out || !ident || !root || (unsigned)InterlockedCompareExchange(&p->key,0,0)!=ident)return false;
    for(int attempt=0;attempt<3;attempt++){
        LONG current=InterlockedCompareExchange(&p->current,0,0);struct ReplicaEmitterCurveBank *bank=&p->banks[current];
        InterlockedIncrement(&bank->readers);
        if(current==InterlockedCompareExchange(&p->current,0,0)){
            struct ReplicaEmitterCurve copy=bank->curve;InterlockedDecrement(&bank->readers);
            if(copy.ident!=ident || copy.root!=root || (unsigned)InterlockedCompareExchange(&p->key,0,0)!=ident)return false;
            *out=copy;return true;
        }
        InterlockedDecrement(&bank->readers);
    }
    return false;
}
static bool replica_emitter_evaluate(const struct ReplicaEmitterCurve *curve,double now,struct ReplicaTimelinePose *out){
    if(!curve || !out || !curve->ident || !curve->root || !curve->timeline.valid || !isfinite(now))return false;
    double target=now-curve->delay;
    if(curve->delay>0 && curve->interpolation.count){
        double source=replica_source_view_time(now,curve->clock_offset,curve->delay);
        const struct ReplicaInterpolationSample *last=replica_interpolation_sample(&curve->interpolation,curve->interpolation.count-1);
        if(source-last->sampled_at>=250)return false;
        *out=replica_interpolation_evaluate(&curve->interpolation,source);
    }else{
        if(target-curve->timeline.sampled_at>=250)return false;
        *out=replica_timeline_evaluate(&curve->timeline,target);
    }
    /* Positions/angles retain the presentation curve. Native particles use
     * authoritative SIM endpoint velocities, not the curve's derivative. */
    struct ReplicaSimulationVelocity velocity;
    if(curve->interpolation.count){
        double source=replica_source_view_time(now,curve->clock_offset,curve->delay);
        velocity=replica_interpolation_sim_velocity(&curve->interpolation,source);
    }else{
        /* Legacy rate-one inputs are already native SIM units. Production
         * acceptance always supplies a buffer, including rate zero and delay
         * zero. Missing converted metadata must skip rather than reconstruct
         * authoritative velocity using a later global rate. */
        if(curve->source_rate!=1)return false;
        velocity=(struct ReplicaSimulationVelocity){curve->timeline.pose.vx,curve->timeline.pose.vy,curve->timeline.pose.angular};
    }
    out->vx=velocity.vx;out->vy=velocity.vy;out->angular=velocity.angular;
    return isfinite(out->x) && isfinite(out->y) && isfinite(out->angle) && isfinite(out->vx) && isfinite(out->vy) && isfinite(out->angular);
}
static void replica_emitter_transform(const struct ReplicaTimelinePose *pose,const double raw[4],float raw_angle,float raw_angular,
                                     float point[2],float velocity[2],float velocity2[2]){
    double angle=replica_timeline_wrap(pose->angle-raw_angle),c=cos(angle),s=sin(angle);
    double x=point[0]-raw[0],y=point[1]-raw[1],offset_x=c*x-s*y,offset_y=s*x+c*y;
    point[0]=(float)(pose->x+offset_x);point[1]=(float)(pose->y+offset_y);
    /* The evaluated cosmetic velocity is already in native SIM units. Keep
     * the nozzle-relative exhaust speed; never divide by a rolling rate. */
    double vx=pose->vx,vy=pose->vy,spin=pose->angular;
    for(int i=0;i<2;i++){
        float *v=i?velocity2:velocity;x=v[0]-raw[2];y=v[1]-raw[3];
        v[0]=(float)(vx+c*x-s*y-(spin-raw_angular)*offset_y);
        v[1]=(float)(vy+s*x+c*y+(spin-raw_angular)*offset_x);
    }
}
#endif
