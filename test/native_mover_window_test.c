#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "../native/mover_window.h"
static void check(bool ok,const char *name){if(!ok){fprintf(stderr,"%s\n",name);exit(1);}}
static void near(float actual,float wanted,const char *name){
    if(!isfinite(actual)||fabsf(actual-wanted)>0.000001f){fprintf(stderr,"%s %.8g expected %.8g\n",name,actual,wanted);exit(1);}
}
static struct ReplicaMoverWindow window,before;
static void interpolation_test(void){
    const struct ReplicaMover a[]={{7,10,0.1f,-1},{7,20,0.4f,0.8f},{9,30,1,0}};
    const struct ReplicaMover b[]={{7,10,0.9f,1},{8,20,0.6f,0.3f},{9,40,0.5f,-0.5f}};
    float out[2];
    check(replica_mover_window_accept(&window,a,3,1000,b,3,1100)==3,"valid authoritative endpoint window");
    check(replica_mover_window_evaluate(&window,7,10,950,out),"startup holds oldest matching control");
    near(out[0],0.1f,"startup acceleration");near(out[1],-1,"startup angular control");
    /* An existing source bracket advances continuously even if no new packet
     * or display preparation arrives between these native mover updates. */
    const double times[]={1000,1016.7,1033.4,1051.1,1067.9,1083.2,1099.9};
    float previous=-1;
    for(unsigned i=0;i<sizeof(times)/sizeof(times[0]);i++){
        double s=(times[i]-1000)/100;
        check(replica_mover_window_evaluate(&window,7,10,times[i],out),"irregular local update interpolation");
        near(out[0],(float)(0.1+0.8*s),"interpolated acceleration");
        near(out[1],(float)(-1+2*s),"angular input interpolates as scalar rather than angle");
        check(out[0]>previous,"controls advance between source arrivals");previous=out[0];
    }
    check(replica_mover_window_evaluate(&window,7,10,1100,out),"exact future boundary takes future control");
    near(out[0],0.9f,"exact endpoint acceleration");near(out[1],1,"exact endpoint angular control");
    check(replica_mover_window_evaluate(&window,7,10,1349.999,out),"latest native endpoint bounded hold");
    check(!replica_mover_window_evaluate(&window,7,10,1350,out),"latest endpoint stale at250ms");near(out[0],0,"stale control zero");

    check(replica_mover_window_evaluate(&window,7,20,1099.999,out),"old owner holds until ownership boundary");
    near(out[0],0.4f,"different actor identity cannot be blended");
    check(!replica_mover_window_evaluate(&window,8,20,1099.999,out),"new owner cannot inherit prior actor controls");
    check(!replica_mover_window_evaluate(&window,7,20,1100,out),"old owner ends at future boundary");
    check(replica_mover_window_evaluate(&window,8,20,1100,out),"new owner begins at future boundary");
    near(out[0],0.6f,"future owner's control");
    check(replica_mover_window_evaluate(&window,9,30,1099.999,out),"removed nozzle holds prior endpoint");
    check(!replica_mover_window_evaluate(&window,9,30,1100,out),"removed nozzle inactive at authoritative boundary");
    check(!replica_mover_window_evaluate(&window,9,40,1099.999,out),"added nozzle inactive before authoritative boundary");
    check(replica_mover_window_evaluate(&window,9,40,1100,out),"added nozzle starts at authoritative boundary");
    near(out[0],0.5f,"added nozzle acceleration");
    check(!replica_mover_window_evaluate(&window,0,10,1050,out),"zero actor cannot lookup a valid nozzle");
    check(!replica_mover_window_evaluate(&window,7,0,1050,out),"zero nozzle identity rejected");
    check(!replica_mover_window_evaluate(&window,7,10,NAN,out),"nonfinite source view time rejected");
}
static void missing_endpoint_test(void){
    const struct ReplicaMover a[]={{7,10,0.6f,-0.3f}},b[]={{7,10,0,0},{7,20,1,1}};
    float out[2];
    check(replica_mover_window_accept(&window,a,1,1000,NULL,0,1000)==1,"no future frame window");
    check(replica_mover_window_evaluate(&window,7,10,1249.999,out),"single endpoint remains valid below horizon");
    near(out[0],0.6f,"single endpoint held control");
    check(!replica_mover_window_evaluate(&window,7,10,1250,out),"single endpoint stale at250ms");
    check(replica_mover_window_accept(&window,a,1,1000,NULL,0,1100)==1,"authoritative empty future frame");
    check(replica_mover_window_evaluate(&window,7,10,1099,out),"hold prior nozzle before empty future frame");
    check(!replica_mover_window_evaluate(&window,7,10,1100,out),"empty future frame removes authority");
    check(replica_mover_window_accept(&window,NULL,0,1000,b,2,1100)==0,"empty prior source frame valid");
    check(!replica_mover_window_evaluate(&window,7,20,1099,out),"missing prior frame emits nothing before future");
    check(replica_mover_window_evaluate(&window,7,20,1100,out),"future nozzle authority starts at boundary");
    check(replica_mover_window_evaluate(&window,7,10,1100,out),"explicit zero control still has authority for native response decay");near(out[0],0,"explicit zero acceleration");
    check(replica_mover_window_accept(&window,a,1,1000,b,2,1000)==1,"equal timestamps endpoint switch accepted");
    check(replica_mover_window_evaluate(&window,7,10,1000,out),"equal timestamps avoid division by zero");near(out[0],0,"equal-time takes future");
    check(replica_mover_window_accept(&window,a,1,1000,NULL,0,1500)==1,"maximum gap boundary accepted");
    check(!replica_mover_window_evaluate(&window,7,10,1250,out),"missing future nozzle retains250ms safety even before its boundary");
    replica_mover_window_clear(&window);
    check(!window.valid&&!window.future&&window.count_a==0&&window.count_b==0,"legacy clear removes timed controls");
    check(!replica_mover_window_evaluate(&window,7,10,1000,out),"cleared window cannot affect legacy fallback");
}
static void rejection_test(void){
    struct ReplicaMover a[]={{7,10,0.2f,-0.3f}},b[]={{7,10,0.8f,0.3f}},bad[]={{7,10,0.2f,0},{7,10,0.5f,0}};
    check(replica_mover_window_accept(&window,a,1,1000,b,1,1100)==1,"validation baseline");before=window;
#define REJECT(expr,name) do{check((expr)<0,name);check(memcmp(&before,&window,sizeof(window))==0,"rejection preserves prior valid window");}while(0)
    REJECT(replica_mover_window_accept(&window,NULL,1,1000,b,1,1100),"missing nonempty rows");
    REJECT(replica_mover_window_accept(&window,a,-1,1000,b,1,1100),"negative row count");
    REJECT(replica_mover_window_accept(&window,a,4097,1000,b,1,1100),"oversized bounded row count");
    REJECT(replica_mover_window_accept(&window,bad,2,1000,b,1,1100),"duplicate persistent nozzle identities");
    bad[1].block_ident=9;REJECT(replica_mover_window_accept(&window,a,1,1000,bad,2,1100),"unsorted future identities");
    a[0].ident=0;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"zero prior actor");a[0].ident=7;
    b[0].block_ident=0;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"zero future nozzle");b[0].block_ident=10;
    a[0].accel=NAN;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"nonfinite acceleration");a[0].accel=0.2f;
    b[0].angular=INFINITY;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"nonfinite future angular control");b[0].angular=0.3f;
    a[0].accel=-.1f;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"negative acceleration");a[0].accel=2;
    REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"acceleration out of bounds");a[0].accel=.2f;
    b[0].angular=-2;REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1100),"angular control out of bounds");b[0].angular=.3f;
    REJECT(replica_mover_window_accept(&window,a,1,NAN,b,1,1100),"nonfinite source timestamp");
    REJECT(replica_mover_window_accept(&window,a,1,0,b,1,1100),"zero source timestamp");
    REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,999),"reversed source timestamps");
    REJECT(replica_mover_window_accept(&window,a,1,1000,b,1,1501),"source gap over500ms");
#undef REJECT
    /* The wire permits tiny float tolerances; emitted controls remain within
     * the native domain rather than amplifying those endpoint tolerances. */
    a[0].accel=b[0].accel=1.0005f;a[0].angular=b[0].angular=-1.0005f;
    check(replica_mover_window_accept(&window,a,1,1000,b,1,1100)==1,"wire precision tolerance accepted");
    float out[2];check(replica_mover_window_evaluate(&window,7,10,1050,out),"clamped interpolation active");
    near(out[0],1,"native acceleration strictly bounded");near(out[1],-1,"native angular input strictly bounded");
}
int main(void){interpolation_test();missing_endpoint_test();rejection_test();puts("source-time mover interpolation, identity boundaries, stale safety and validation passed");return 0;}
