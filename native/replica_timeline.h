/* Pure presentation math shared by the native client and executable tests.
 * Legacy correction timestamps use the client monotonic clock. Buffered
 * samples retain the immutable server clock; only evaluation maps clocks.
 * The authoritative body is never changed by these functions. */
#ifndef REPOPULATED_REPLICA_TIMELINE_H
#define REPOPULATED_REPLICA_TIMELINE_H
#include <math.h>
#include <stdbool.h>
struct ReplicaTimelinePose { double x,y,vx,vy,angle,angular; };
struct ReplicaTimeline {
    struct ReplicaTimelinePose pose;
    double sampled_at,corrected_at,dx,dy,da,dvx,dvy,dw;
    bool valid;
};
struct ReplicaSimulationClock {double wall[4],sim[4],rate;unsigned next,count;};
/* Samples retain native SIM velocities and the immutable conversion used for
 * their wall-time Hermite tangents. Cosmetic particles need the former. */
struct ReplicaInterpolationSample {struct ReplicaTimelinePose pose;double sampled_at,source_rate;};
struct ReplicaInterpolationBuffer {struct ReplicaInterpolationSample samples[12];unsigned count,next;};
struct ReplicaSimulationVelocity {double vx,vy,angular;};
static double replica_source_view_time(double local_now,double clock_offset,double delay){return local_now-clock_offset-delay;}
static void replica_simulation_clock_accept(struct ReplicaSimulationClock *clock,double wall,double sim){
    if(clock->count){
        unsigned previous=(clock->next+3)%4;
        if(wall<=clock->wall[previous] || sim<clock->sim[previous])return;
        if(wall-clock->wall[previous]>500)clock->count=0;
    }
    unsigned slot=clock->next;clock->wall[slot]=wall;clock->sim[slot]=sim;
    clock->next=(slot+1)%4;if(clock->count<4)clock->count++;
    if(clock->count<2){clock->rate=0;return;}
    unsigned oldest=(clock->next+4-clock->count)%4;
    double elapsed=wall-clock->wall[oldest];
    /* Zone time advances by native physics steps. Background games can run
     * fewer steps while still displaying 60 frames: velocity is per sim
     * second, not per wall second. Average four samples to suppress rounded
     * millisecond/tick quantization without a long response to focus changes. */
    clock->rate=elapsed>0?fmax(0,fmin(4,(sim-clock->sim[oldest])/elapsed)):0;
}
static double replica_timeline_wrap(double a){return remainder(a,6.283185307179586);}
static const struct ReplicaInterpolationSample *replica_interpolation_sample(const struct ReplicaInterpolationBuffer *b,unsigned index){
    return &b->samples[(b->next+12-b->count+index)%12];
}
static bool replica_interpolation_accept_sim(struct ReplicaInterpolationBuffer *b,struct ReplicaTimelinePose pose,double sampled_at,double source_rate){
    if(!isfinite(source_rate) || source_rate<0 || source_rate>4)return false;
    bool reset=false;
    if(b->count){
        const struct ReplicaInterpolationSample *last=replica_interpolation_sample(b,b->count-1);
        if(sampled_at<=last->sampled_at)return false;
        double dx=pose.x-last->pose.x,dy=pose.y-last->pose.y;
        if(sampled_at-last->sampled_at>500 || dx*dx+dy*dy>1000000 || fabs(replica_timeline_wrap(pose.angle-last->pose.angle))>2.5){b->count=0;b->next=0;reset=true;}
    }
    b->samples[b->next]=(struct ReplicaInterpolationSample){pose,sampled_at,source_rate};
    b->next=(b->next+1)%12;if(b->count<12)b->count++;
    return reset;
}
static inline bool replica_interpolation_accept(struct ReplicaInterpolationBuffer *b,struct ReplicaTimelinePose pose,double sampled_at){
    return replica_interpolation_accept_sim(b,pose,sampled_at,1);
}
static struct ReplicaTimelinePose replica_interpolation_wall_pose(const struct ReplicaInterpolationSample *s){
    struct ReplicaTimelinePose p=s->pose;p.vx*=s->source_rate;p.vy*=s->source_rate;p.angular*=s->source_rate;return p;
}
static struct ReplicaSimulationVelocity replica_interpolation_sim_velocity(const struct ReplicaInterpolationBuffer *b,double target){
    struct ReplicaSimulationVelocity velocity={0};if(!b->count || !isfinite(target))return velocity;
    const struct ReplicaInterpolationSample *first=replica_interpolation_sample(b,0);
    if(target<first->sampled_at)return (struct ReplicaSimulationVelocity){first->pose.vx,first->pose.vy,first->pose.angular};
    for(unsigned i=1;i<b->count;i++){
        const struct ReplicaInterpolationSample *a=replica_interpolation_sample(b,i-1),*c=replica_interpolation_sample(b,i);
        if(target>=c->sampled_at)continue;
        double s=(target-a->sampled_at)/(c->sampled_at-a->sampled_at);
        return (struct ReplicaSimulationVelocity){a->pose.vx+(c->pose.vx-a->pose.vx)*s,
            a->pose.vy+(c->pose.vy-a->pose.vy)*s,a->pose.angular+(c->pose.angular-a->pose.angular)*s};
    }
    const struct ReplicaInterpolationSample *last=replica_interpolation_sample(b,b->count-1);
    if(target-last->sampled_at>=250)return velocity;
    return (struct ReplicaSimulationVelocity){last->pose.vx,last->pose.vy,last->pose.angular};
}
static struct ReplicaTimelinePose replica_interpolation_evaluate(const struct ReplicaInterpolationBuffer *b,double target){
    struct ReplicaTimelinePose p={0};if(!b->count)return p;
    const struct ReplicaInterpolationSample *first=replica_interpolation_sample(b,0);
    if(target<first->sampled_at){p=first->pose;p.vx=0;p.vy=0;p.angular=0;return p;}
    for(unsigned i=1;i<b->count;i++){
        const struct ReplicaInterpolationSample *a=replica_interpolation_sample(b,i-1),*c=replica_interpolation_sample(b,i);
        if(target>=c->sampled_at)continue;
        double duration=(c->sampled_at-a->sampled_at)/1000,s=(target-a->sampled_at)/(c->sampled_at-a->sampled_at),s2=s*s,s3=s2*s;
        double h00=2*s3-3*s2+1,h10=s3-2*s2+s,h01=-2*s3+3*s2,h11=s3-s2;
        double d00=(6*s2-6*s)/duration,d10=3*s2-4*s+1,d01=(-6*s2+6*s)/duration,d11=3*s2-2*s;
        double next_angle=a->pose.angle+replica_timeline_wrap(c->pose.angle-a->pose.angle);
        struct ReplicaTimelinePose wall_a=replica_interpolation_wall_pose(a),wall_c=replica_interpolation_wall_pose(c);
        p.x=h00*a->pose.x+h10*duration*wall_a.vx+h01*c->pose.x+h11*duration*wall_c.vx;
        p.y=h00*a->pose.y+h10*duration*wall_a.vy+h01*c->pose.y+h11*duration*wall_c.vy;
        p.angle=h00*a->pose.angle+h10*duration*wall_a.angular+h01*next_angle+h11*duration*wall_c.angular;
        p.vx=d00*a->pose.x+d10*wall_a.vx+d01*c->pose.x+d11*wall_c.vx;
        p.vy=d00*a->pose.y+d10*wall_a.vy+d01*c->pose.y+d11*wall_c.vy;
        p.angular=d00*a->pose.angle+d10*wall_a.angular+d01*next_angle+d11*wall_c.angular;
        return p;
    }
    const struct ReplicaInterpolationSample *last=replica_interpolation_sample(b,b->count-1);p=replica_interpolation_wall_pose(last);
    double elapsed=fmax(0,fmin(250,target-last->sampled_at))/1000;
    p.x+=p.vx*elapsed;p.y+=p.vy*elapsed;p.angle+=p.angular*elapsed;
    if(target-last->sampled_at>=250){p.vx=0;p.vy=0;p.angular=0;}
    return p;
}
static struct ReplicaTimelinePose replica_timeline_evaluate(const struct ReplicaTimeline *t,double now){
    struct ReplicaTimelinePose p=t->pose;
    double age=fmax(0,fmin(0.25,(now-t->sampled_at)/1000));
    p.x+=p.vx*age;p.y+=p.vy*age;p.angle+=p.angular*age;
    /* Preserve position and velocity across packet arrival. A Hermite decay
     * removes the correction over 100ms without changing speed instantly. */
    double s=fmax(0,fmin(1,(now-t->corrected_at)/100)),s2=s*s,s3=s2*s;
    double h=2*s3-3*s2+1,k=(s3-2*s2+s)*0.1;
    double dh=(6*s2-6*s)/0.1,dk=3*s2-4*s+1;
    p.x+=t->dx*h+t->dvx*k;p.y+=t->dy*h+t->dvy*k;p.angle+=t->da*h+t->dw*k;
    p.vx+=t->dx*dh+t->dvx*dk;p.vy+=t->dy*dh+t->dvy*dk;p.angular+=t->da*dh+t->dw*dk;
    if(now-t->sampled_at>=250){p.vx=0;p.vy=0;p.angular=0;}
    return p;
}
static void replica_timeline_accept(struct ReplicaTimeline *t,struct ReplicaTimelinePose pose,double sampled_at,double now){
    struct ReplicaTimelinePose before=replica_timeline_evaluate(t,now);
    bool valid=t->valid;
    t->pose=pose;t->sampled_at=sampled_at;t->corrected_at=now;
    t->dx=0;t->dy=0;t->da=0;t->dvx=0;t->dvy=0;t->dw=0;t->valid=true;
    if(!valid)return;
    struct ReplicaTimelinePose after=replica_timeline_evaluate(t,now);
    double dx=before.x-after.x,dy=before.y-after.y,da=replica_timeline_wrap(before.angle-after.angle);
    /* Sector teleports and respawns must not be animated through the galaxy. */
    if(dx*dx+dy*dy>1000000 || fabs(da)>2.5)return;
    t->dx=dx;t->dy=dy;t->da=da;
    t->dvx=before.vx-after.vx;t->dvy=before.vy-after.vy;t->dw=before.angular-after.angular;
}
#endif
