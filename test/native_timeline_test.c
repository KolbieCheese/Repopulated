#include <stdio.h>
#include <stdlib.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#undef near
#include "../native/monotonic_clock.h"
#include "../native/replica_timeline.h"
static void near(double actual,double wanted,double tolerance,const char *name){
    if(!isfinite(actual) || fabs(actual-wanted)>tolerance){fprintf(stderr,"%s: %.12g expected %.12g\n",name,actual,wanted);exit(1);}
}
int main(void){
    double previous=RepopulatedMonotonicMillis();int distinct=0;
    for(int i=0;i<10000;i++){
        double now=RepopulatedMonotonicMillis();
        if(now<previous){fputs("presentation clock moved backward\n",stderr);return 1;}
        if(now>previous)distinct++;previous=now;
    }
    if(distinct<100){fprintf(stderr,"presentation clock still quantized: %d distinct samples\n",distinct);return 1;}
    struct ReplicaSimulationClock clock={0};
    for(int i=0;i<10;i++)replica_simulation_clock_accept(&clock,1000+i*100,5000+i*43);
    near(clock.rate,0.43,1e-12,"background simulation pace");
    /* A client must travel 80.47 world units in one wall second when the
     * server's 187.14-unit native velocity advances at 43% simulation pace. */
    struct ReplicaTimeline slowed={0};
    struct ReplicaTimelinePose slow_pose={0,0,187.14*clock.rate,0,0,2*clock.rate};
    replica_timeline_accept(&slowed,slow_pose,1000,1000);
    struct ReplicaTimelinePose slow_shown=replica_timeline_evaluate(&slowed,1100);
    near(slow_shown.x,187.14*0.043,1e-10,"sim-time velocity projection");
    near(slow_shown.angle,0.086,1e-12,"sim-time angular projection");
    for(int i=10;i<14;i++)replica_simulation_clock_accept(&clock,1000+i*100,5430+(i-10)*100);
    near(clock.rate,1,1e-12,"foreground simulation pace recovery");
    for(int i=14;i<18;i++)replica_simulation_clock_accept(&clock,1000+i*100,5730);
    near(clock.rate,0,1e-12,"paused simulation pace");
    struct ReplicaTimeline timeline={0};
    /* Native motion timestamps, unlike receipt timestamps, retain uniform
     * travel when delivery arrives with varying 0..60ms delay. */
    const double arrived[]={0,75,117,170,261,268,341,403,428,494,572,598};
    int next=0;
    for(int ms=0;ms<=650;ms++){
        while(next<12 && arrived[next]<=ms){
            double source=next*50;
            struct ReplicaTimelinePose pose={10+source*0.3,-4+source*0.1,300,100,3.1+source*0.001,1};
            pose.angle=replica_timeline_wrap(pose.angle);
            replica_timeline_accept(&timeline,pose,source,ms);next++;
        }
        struct ReplicaTimelinePose shown=replica_timeline_evaluate(&timeline,ms);
        near(shown.x,10+ms*0.3,1e-8,"irregular delivery x");near(shown.y,-4+ms*0.1,1e-8,"irregular delivery y");
        near(replica_timeline_wrap(shown.angle-(3.1+ms*0.001)),0,1e-8,"wrapped angular continuity");
        near(shown.vx,300,1e-8,"constant speed");
    }
    /* Corrections preserve both position and velocity at their start. */
    struct ReplicaTimelinePose before=replica_timeline_evaluate(&timeline,660);
    struct ReplicaTimelinePose changed={250,15,120,-30,-2.4,-0.5};
    replica_timeline_accept(&timeline,changed,650,660);
    struct ReplicaTimelinePose after=replica_timeline_evaluate(&timeline,660);
    near(after.x,before.x,1e-8,"correction position");near(after.vx,before.vx,1e-8,"correction velocity");
    near(replica_timeline_wrap(after.angle-before.angle),0,1e-8,"correction angle");near(after.angular,before.angular,1e-8,"correction angular speed");
    after=replica_timeline_evaluate(&timeline,760);
    near(after.x,changed.x+changed.vx*0.11,1e-8,"correction completion position");near(after.vx,changed.vx,1e-8,"correction completion speed");
    /* 250ms stale extrapolation freezes; no continuing runaway flight. */
    before=replica_timeline_evaluate(&timeline,1000);after=replica_timeline_evaluate(&timeline,2000);
    near(after.x,before.x,1e-8,"stale position freeze");near(after.vx,0,0,"stale velocity freeze");
    struct ReplicaInterpolationBuffer buffered={0};next=0;
    for(int ms=0;ms<=650;ms++){
        while(next<12 && arrived[next]<=ms){
            double source=next*50;
            struct ReplicaTimelinePose pose={10+source*0.3,-4+source*0.1,300,100,replica_timeline_wrap(3.1+source*0.001),1};
            replica_interpolation_accept(&buffered,pose,source);next++;
        }
        if(ms<100)continue;
        double target=ms-100;struct ReplicaTimelinePose shown=replica_interpolation_evaluate(&buffered,target);
        near(shown.x,10+target*0.3,1e-8,"buffered irregular delivery x");near(shown.y,-4+target*0.1,1e-8,"buffered irregular delivery y");
        near(replica_timeline_wrap(shown.angle-(3.1+target*0.001)),0,1e-8,"buffered shortest wrapped angle");
        near(shown.vx,300,1e-8,"buffered constant speed");
    }
    struct ReplicaInterpolationBuffer curve={0};
    replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){0,0,0,0,3.1,1},0);
    replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){5,2,40,5,replica_timeline_wrap(3.15),0.5},50);
    replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){8,4,0,0,replica_timeline_wrap(3.16),0.7},100);
    before=replica_interpolation_evaluate(&curve,50-1e-6);after=replica_interpolation_evaluate(&curve,50+1e-6);
    near(after.x,before.x,1e-6,"buffered C1 position");near(after.vx,before.vx,1e-3,"buffered C1 velocity");
    near(replica_timeline_wrap(after.angle-before.angle),0,1e-6,"buffered C1 wrapped angle");near(after.angular,before.angular,1e-3,"buffered C1 angular velocity");
    before=replica_interpolation_evaluate(&curve,25);
    replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){20,9,80,10,3.2,1},150);
    after=replica_interpolation_evaluate(&curve,25);
    near(after.x,before.x,0,"future bracket arrival invisible position");near(after.vx,before.vx,0,"future bracket arrival invisible velocity");
    /* Host and client may boot at unrelated clock epochs. Refining the clock
     * offset must translate the evaluation target, never retime individual
     * stored endpoints or compress a newly arriving bracket. */
    struct ReplicaInterpolationBuffer source_clock={0};
    const double epoch=900000000,delay=100,offsets[]={137.5,121.25,117.125,-800000000.75};
    replica_interpolation_accept(&source_clock,(struct ReplicaTimelinePose){0,0,0,0,3.1,1},epoch);
    replica_interpolation_accept(&source_clock,(struct ReplicaTimelinePose){5,2,40,5,replica_timeline_wrap(3.15),0.5},epoch+50);
    before=replica_interpolation_evaluate(&source_clock,epoch+25);
    for(int i=0;i<4;i++){
        double local_now=epoch+25+offsets[i]+delay;
        double target=replica_source_view_time(local_now,offsets[i],delay);
        after=replica_interpolation_evaluate(&source_clock,target);
        near(target,epoch+25,0,"clock refinement source target");
        near(after.x,before.x,0,"clock refinement invariant curve position");
        near(after.vx,before.vx,0,"clock refinement invariant curve velocity");
        near(replica_interpolation_sample(&source_clock,0)->sampled_at,epoch,0,"immutable first source anchor");
        near(replica_interpolation_sample(&source_clock,1)->sampled_at,epoch+50,0,"immutable second source anchor");
        near(target-epoch,25,0,"visual source age uses same clock refinement");
    }
    replica_interpolation_accept(&source_clock,(struct ReplicaTimelinePose){8,4,0,0,3.16,0.7},epoch+100);
    after=replica_interpolation_evaluate(&source_clock,replica_source_view_time(epoch+25+offsets[3]+delay,offsets[3],delay));
    near(after.x,before.x,0,"offset refinement plus new bracket arrival invisible");
    before=replica_interpolation_evaluate(&curve,400);after=replica_interpolation_evaluate(&curve,2000);
    near(after.x,before.x,0,"buffered stale freeze");near(after.vx,0,0,"buffered stale velocity");
    if(!replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){12,2,0,0,3.2,0},900) || curve.count!=1){fputs("stale buffer did not reset\n",stderr);return 1;}
    after=replica_interpolation_evaluate(&curve,850);near(after.x,12,0,"startup after stale gap holds new sample");
    if(!replica_interpolation_accept(&curve,(struct ReplicaTimelinePose){2012,2,0,0,3.2,0},950) || curve.count!=1){fputs("teleport buffer did not reset\n",stderr);return 1;}
    for(int i=12;i<=20;i++)replica_interpolation_accept(&buffered,(struct ReplicaTimelinePose){10+i*50*0.3,-4+i*50*0.1,300,100,replica_timeline_wrap(3.1+i*50*0.001),1},i*50);
    if(buffered.count!=12){fputs("interpolation ring capacity changed\n",stderr);return 1;}
    after=replica_interpolation_evaluate(&buffered,825);near(after.x,10+825*0.3,1e-8,"circular buffer bracket lookup");
    puts("Native timeline: irregular delivery, wrapped rotation, correction continuity and stale freeze passed.");
    return 0;
}
