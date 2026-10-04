#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#undef near
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../native/replica_timeline.h"
#include "../native/emitter_curve.h"

static void check(bool ok,const char *name){if(!ok){fprintf(stderr,"%s\n",name);exit(1);}}
static void near(double actual,double wanted,double tolerance,const char *name){
    if(!isfinite(actual)||fabs(actual-wanted)>tolerance){fprintf(stderr,"%s: %.12g expected %.12g\n",name,actual,wanted);exit(1);}
}
static struct ReplicaEmitterCurve curve_at(double offset){
    struct ReplicaEmitterCurve curve={.ident=0x70000002,.root=0x12345678,.clock_offset=offset,.delay=100,.source_rate=0.5};
    const double times[]={1000,1047,1113,1150,1209,1261,1310,1370};
    for(unsigned i=0;i<sizeof(times)/sizeof(times[0]);i++){
        double seconds=(times[i]-1000)/1000;
        struct ReplicaTimelinePose pose={10+seconds*200,-20+seconds*50,200,50,replica_timeline_wrap(3.1+seconds*0.7),0.7};
        struct ReplicaTimelinePose native_pose=pose;native_pose.vx=400;native_pose.vy=100;native_pose.angular=1.4;
        replica_interpolation_accept_sim(&curve.interpolation,native_pose,times[i],0.5);
        replica_timeline_accept(&curve.timeline,pose,times[i]+offset,times[i]+offset);
    }
    return curve;
}
static void math_test(void){
    struct ReplicaEmitterCurve curve=curve_at(900000),shifted=curve_at(-400000);
    struct ReplicaTimelinePose pose,other,prepared;
    check(replica_emitter_evaluate(&curve,901250,&prepared),"initial emission evaluation");
    /* Render preparations may stop for 100 ms. Emissions must nevertheless
     * move on the same source-clock curve instead of overlapping at the last
     * prepared render origin. Unrelated host/client QPC epochs are harmless. */
    double previous=prepared.x;
    for(int ms=1251;ms<=1350;ms++){
        check(replica_emitter_evaluate(&curve,900000+ms,&pose),"sparse-render curve evaluation");
        check(replica_emitter_evaluate(&shifted,-400000+ms,&other),"different clock epoch evaluation");
        near(pose.x,10+(ms-1100)*0.2,1e-9,"emission source-clock position");
        near(pose.y,-20+(ms-1100)*0.05,1e-9,"emission source-clock y");
        near(pose.vx,400,1e-9,"emission native SIM velocity");
        near(pose.vy,100,1e-9,"emission native SIM velocity y");
        near(pose.angular,1.4,1e-9,"emission native SIM angular velocity");
        near(replica_timeline_wrap(pose.angle-(3.1+(ms-1100)*0.0007)),0,1e-9,"emission wrapped angle");
        near(pose.x,other.x,0,"unrelated process-clock epoch");
        check(pose.x>previous,"emission must advance without render preparation");previous=pose.x;
    }
    near(pose.x-prepared.x,20,1e-8,"last prepared origin would lag by twenty units");
    check(replica_emitter_evaluate(&curve,901719,&pose),"bounded extrapolation below horizon");
    check(!replica_emitter_evaluate(&curve,901720,&pose),"stale origin cannot emit at frozen horizon");
    curve.delay=0;curve.interpolation.count=0;curve.source_rate=1;
    check(replica_emitter_evaluate(&curve,901619,&pose),"legacy correction below horizon");
    check(!replica_emitter_evaluate(&curve,901620,&pose),"legacy correction stale rejection");
    curve.source_rate=0;
    check(!replica_emitter_evaluate(&curve,901619,&pose),"missing rate-zero native metadata cannot invent a baseline");
    curve.source_rate=0.5;
    check(!replica_emitter_evaluate(&curve,901619,&pose),"missing native metadata cannot use rolling-rate reconstruction");
    curve.source_rate=1;
    curve.timeline.valid=false;
    check(!replica_emitter_evaluate(&curve,901400,&pose),"uninitialized curve rejected");
    curve.timeline.valid=true;
    check(!replica_emitter_evaluate(&curve,NAN,&pose),"nonfinite emission clock rejected");

    const double raw[]={20,40,300,50};
    struct ReplicaTimelinePose rotated={100,200,300,50,-3.1,0.6};
    float point[]={25,42},velocity[]={280,70},velocity2[]={310,10};
    double angle=replica_timeline_wrap(rotated.angle-3.1),c=cos(angle),s=sin(angle),x=c*5-s*2,y=s*5+c*2;
    replica_emitter_transform(&rotated,raw,3.1f,0.2f,point,velocity,velocity2);
    /* The cosmetic baseline is already in native SIM units. Conversion must
     * not be applied again by the nozzle transform. */
    near(point[0],100+x,2e-5,"rotated local nozzle x");near(point[1],200+y,2e-5,"rotated local nozzle y");
    near(velocity[0],300+c*(-20)-s*20-(0.6-0.2)*y,2e-5,"buffered first particle velocity x");
    near(velocity[1],50+s*(-20)+c*20+(0.6-0.2)*x,2e-5,"buffered first particle velocity y");
    near(velocity2[0],300+c*10-s*(-40)-(0.6-0.2)*y,2e-5,"buffered second particle velocity x");
    near(velocity2[1],50+s*10+c*(-40)+(0.6-0.2)*x,2e-5,"buffered second particle velocity y");
    /* A zero baseline still preserves nozzle-relative velocities, without
     * any conversion or division by a zero source rate. */
    rotated.vx=0;rotated.vy=0;rotated.angular=0;
    point[0]=25;point[1]=42;velocity[0]=280;velocity[1]=70;velocity2[0]=310;velocity2[1]=10;
    replica_emitter_transform(&rotated,raw,3.1f,0.2f,point,velocity,velocity2);
    check(isfinite(velocity[0])&&isfinite(velocity2[1]),"paused source finite velocities");
}
static void velocity_test(void){
    struct ReplicaEmitterCurve curve={.ident=0x70000002,.root=0x12345678,.clock_offset=900000,.delay=100,.source_rate=4};
    struct ReplicaTimelinePose a={0,0,100,-50,3.1,1},b={20,5,300,50,-3.1,3};
    replica_interpolation_accept_sim(&curve.interpolation,a,1000,0.25);
    replica_interpolation_accept_sim(&curve.interpolation,b,1100,1.5);
    struct ReplicaTimelinePose wall=b;wall.vx*=1.5;wall.vy*=1.5;wall.angular*=1.5;
    replica_timeline_accept(&curve.timeline,wall,901100,901100);
    struct ReplicaTimelinePose shown,emission;
    shown=replica_interpolation_evaluate(&curve.interpolation,1050);
    check(replica_emitter_evaluate(&curve,901150,&emission),"rate-transition cosmetic evaluation");
    near(emission.x,shown.x,0,"SIM baseline leaves Hermite birth position unchanged");
    near(emission.y,shown.y,0,"SIM baseline leaves Hermite birth y unchanged");
    near(emission.angle,shown.angle,0,"SIM baseline leaves shortest wrapped angle unchanged");
    near(emission.vx,200,1e-12,"native endpoint velocity interpolated across rates");
    near(emission.vy,0,1e-12,"native endpoint y velocity interpolated across rates");
    near(emission.angular,2,1e-12,"native endpoint angular velocity interpolated across wrapped angle");
    check(fabs(shown.vx-emission.vx)>1,"Hermite correction derivative must not become particle baseline");
    check(fabs(shown.angular-emission.angular)>1,"wrapped angle derivative must not become native angular baseline");
    curve.source_rate=0.01;
    struct ReplicaTimelinePose independent;
    check(replica_emitter_evaluate(&curve,901150,&independent),"rolling rate independent cosmetic evaluation");
    near(independent.vx,emission.vx,0,"rolling rate cannot rescale immutable endpoint velocity");
    near(independent.angular,emission.angular,0,"rolling rate cannot rescale immutable endpoint spin");
    struct ReplicaSimulationVelocity native=replica_interpolation_sim_velocity(&curve.interpolation,900);
    near(native.vx,100,0,"before-buffer endpoint velocity");
    native=replica_interpolation_sim_velocity(&curve.interpolation,1349);
    near(native.vx,300,0,"late frame holds last native velocity within horizon");
    check(!replica_emitter_evaluate(&curve,901450,&emission),"native velocity cannot bypass stale origin horizon");
    native=replica_interpolation_sim_velocity(&curve.interpolation,1350);
    near(native.vx,0,0,"native velocity freezes at bounded horizon");

    struct ReplicaInterpolationBuffer paused={0};
    replica_interpolation_accept_sim(&paused,a,1000,0);
    replica_interpolation_accept_sim(&paused,b,1100,0);
    native=replica_interpolation_sim_velocity(&paused,1050);
    near(native.vx,200,0,"zero SIM rate preserves native endpoint velocity information");
    near(native.angular,2,0,"zero SIM rate preserves native endpoint spin information");
    const struct ReplicaInterpolationSample *sample=replica_interpolation_sample(&paused,0);
    near(sample->source_rate,0,0,"paused sample rate immutable");
    near(replica_interpolation_wall_pose(sample).vx,0,0,"paused sample still has zero rendering tangent");
    curve.interpolation=paused;curve.source_rate=0;
    curve.timeline.pose=b;curve.timeline.pose.vx=0;curve.timeline.pose.vy=0;curve.timeline.pose.angular=0;
    check(replica_emitter_evaluate(&curve,901150,&emission),"production rate-zero buffer remains usable");
    near(emission.vx,200,0,"production paused buffer retains authoritative velocity");
    near(emission.angular,2,0,"production paused buffer retains authoritative spin");
    curve.delay=0;
    check(replica_emitter_evaluate(&curve,901150,&emission),"production delay-zero buffer remains usable");
    near(emission.vx,300,0,"delay-zero particle uses latest authoritative native endpoint");
    struct ReplicaInterpolationBuffer legacy={0};
    replica_interpolation_accept(&legacy,a,1000);
    near(replica_interpolation_sample(&legacy,0)->source_rate,1,0,"legacy velocity sample defaults to rate one");
    unsigned count=paused.count;
    replica_interpolation_accept_sim(&paused,a,1150,NAN);
    check(paused.count==count,"invalid rate cannot contaminate immutable samples");
}
static void unchanged_curve_test(void){
    struct ReplicaInterpolationBuffer old_wall={0},new_sim={0};
    const double times[]={1000,1047,1113,1150,1209,1261,1310,1370};
    const double rates[]={0.25,0.5,1.5,0,0.7,1,0.3,2};
    for(unsigned i=0;i<sizeof(times)/sizeof(times[0]);i++){
        double t=(times[i]-1000)/1000;
        struct ReplicaTimelinePose sim={t*200,t*t*40,200+i*5,-50+i*10,replica_timeline_wrap(3.1+t),1+i*0.1},wall=sim;
        wall.vx*=rates[i];wall.vy*=rates[i];wall.angular*=rates[i];
        replica_interpolation_accept(&old_wall,wall,times[i]);
        replica_interpolation_accept_sim(&new_sim,sim,times[i],rates[i]);
    }
    for(int at=950;at<=1620;at++){
        struct ReplicaTimelinePose old=replica_interpolation_evaluate(&old_wall,at),next=replica_interpolation_evaluate(&new_sim,at);
        near(next.x,old.x,0,"stored native velocity does not change displayed x");
        near(next.y,old.y,0,"stored native velocity does not change displayed y");
        near(next.angle,old.angle,0,"stored native velocity does not change displayed wrapped angle");
        near(next.vx,old.vx,0,"stored native velocity does not change displayed x tangent");
        near(next.vy,old.vy,0,"stored native velocity does not change displayed y tangent");
        near(next.angular,old.angular,0,"stored native velocity does not change displayed angular tangent");
    }
}
static struct ReplicaEmitterCurve version(unsigned n){
    struct ReplicaEmitterCurve c={.ident=0x70000002,.root=0x12345678,.clock_offset=n,.delay=100,.source_rate=1};
    c.timeline.valid=true;c.timeline.sampled_at=n;c.timeline.pose=(struct ReplicaTimelinePose){n,n*2.,n*3.,n*4.,n*5.,n*6.};
    for(unsigned i=0;i<12;i++)replica_interpolation_accept(&c.interpolation,c.timeline.pose,n*1000.+i);
    return c;
}
static bool same_version(const struct ReplicaEmitterCurve *c){
    double n=c->clock_offset;
    if(c->timeline.sampled_at!=n||c->timeline.pose.x!=n||c->timeline.pose.y!=n*2||c->timeline.pose.vx!=n*3||c->timeline.pose.vy!=n*4||c->timeline.pose.angle!=n*5||c->timeline.pose.angular!=n*6)return false;
    if(c->interpolation.count!=12)return false;
    for(unsigned i=0;i<12;i++){
        const struct ReplicaInterpolationSample *s=replica_interpolation_sample(&c->interpolation,i);
        if(s->sampled_at!=n*1000.+i||s->pose.x!=n||s->pose.y!=n*2||s->pose.angular!=n*6||s->source_rate!=1)return false;
    }
    return true;
}
static void publication_test(void){
    struct ReplicaEmitterPublication p={0};struct ReplicaEmitterCurve out=version(99),before=out,a=version(1),b=version(2);
    check(!replica_emitter_copy(&p,a.ident,a.root,&out),"unpublished curve skipped");
    check(memcmp(&out,&before,sizeof(out))==0,"unavailable snapshot leaves output untouched");
    check(replica_emitter_publish(&p,&a),"first publication");
    check(replica_emitter_copy(&p,a.ident,a.root,&out)&&same_version(&out),"complete first immutable snapshot");
    check(!replica_emitter_copy(&p,a.ident,a.root+1,&out),"reused pointer generation rejected");
    check(!replica_emitter_copy(&p,a.ident+1,a.root,&out),"different actor identity rejected");
    LONG old=p.current;InterlockedIncrement(&p.banks[old].readers);
    check(replica_emitter_publish(&p,&b),"new publication while old bank pinned");
    check(p.banks[old].curve.clock_offset==1,"pinned old snapshot not overwritten");
    LONG current=p.current;
    for(int i=0;i<3;i++)if(i!=current&&i!=old)InterlockedIncrement(&p.banks[i].readers);
    check(!replica_emitter_publish(&p,&a),"all spare banks pinned causes bounded publish skip");
    check(replica_emitter_copy(&p,b.ident,b.root,&out)&&out.clock_offset==2,"publication miss leaves current readable");
    for(int i=0;i<3;i++)if(i!=current)InterlockedDecrement(&p.banks[i].readers);
    InterlockedExchange(&p.key,0);
    check(!replica_emitter_copy(&p,b.ident,b.root,&out),"native removal invalidates published key");
}
static struct ReplicaEmitterPublication shared;
static volatile LONG finished,failed,successes;
static DWORD WINAPI writer(void *arg){
    (void)arg;
    for(unsigned n=1;n<=50000;n++){struct ReplicaEmitterCurve c=version(n);replica_emitter_publish(&shared,&c);if(!(n%512))SwitchToThread();}
    InterlockedExchange(&finished,1);return 0;
}
static DWORD WINAPI reader(void *arg){
    (void)arg;struct ReplicaEmitterCurve c;
    while(!InterlockedCompareExchange(&finished,0,0)){
        if(replica_emitter_copy(&shared,0x70000002,0x12345678,&c)){
            if(!same_version(&c))InterlockedExchange(&failed,1);
            InterlockedIncrement(&successes);
        }
    }
    return 0;
}
static void concurrent_test(void){
    struct ReplicaEmitterCurve c=version(1);check(replica_emitter_publish(&shared,&c),"concurrent initial publication");
    HANDLE threads[4];for(int i=0;i<3;i++)threads[i]=CreateThread(NULL,0,reader,NULL,0,NULL);
    threads[3]=CreateThread(NULL,0,writer,NULL,0,NULL);
    for(int i=0;i<4;i++)check(threads[i]!=NULL,"native worker created");
    check(WaitForMultipleObjects(4,threads,TRUE,10000)==WAIT_OBJECT_0,"publication stress completed without waiting on readers");
    for(int i=0;i<4;i++)CloseHandle(threads[i]);
    check(!failed&&successes>100,"successful concurrent snapshots never tear");
    printf("immutable concurrent snapshots: %ld\n",(long)successes);
}
int main(void){math_test();velocity_test();unchanged_curve_test();publication_test();concurrent_test();puts("emission clock, wrapped rotation, immutable SIM velocities, unchanged curve and bounded publication passed");return 0;}
