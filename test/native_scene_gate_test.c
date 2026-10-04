#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
static DWORD fake_thread=1;
static double fake_time;
static unsigned fatal_calls,wait_calls;
static bool finish_on_sleep;
static int replica_scene_end(void *zone,int committed);
static DWORD fake_thread_id(void){return fake_thread;}
static double replica_now_millis(void){return fake_time;}
static DWORD fake_wait(HANDLE event,DWORD delay){
    (void)event;wait_calls++;
    if(finish_on_sleep){fake_time+=1;fake_thread=1;replica_scene_end((void*)1,1);fake_thread=2;finish_on_sleep=false;return WAIT_OBJECT_0;}
    fake_time+=delay;return WAIT_TIMEOUT;
}
#define GetCurrentThreadId fake_thread_id
#define WaitForSingleObject fake_wait
#define REPLICA_SCENE_FATAL(reason) ((void)(reason),fatal_calls++)
#include "../native/scene_gate.h"
static void check(bool good,const char *name){if(!good){fprintf(stderr,"Scene gate: %s\n",name);exit(1);}}
int main(int argc,char **argv){
    if(argc==2 && !strcmp(argv[1],"--fatal")){
        replica_scene_gate_fatal("private process fail-closed regression");return 99;
    }
    double stats[8];
    replica_scene_idle_begin();replica_scene_idle_end();
    check(replica_scene_gate.state==REPLICA_SCENE_DISABLED,"disabled pacing never enables a gate");
    check(replica_scene_configure(NULL)==-1 && replica_scene_configure((void*)1)==1,"owned zone configure");
    check(replica_scene_configure((void*)1)==1 && replica_scene_configure((void*)2)==-1,"configure once preserves owner");
    fake_thread=2;check(replica_scene_configure((void*)1)==-1,"foreign configure refused");
    check(replica_scene_try_begin((void*)1)==-1,"foreign source refused");fake_thread=1;
    check(replica_scene_try_begin((void*)2)==-1,"foreign zone refused");
    check(replica_scene_try_begin((void*)1)==0 && wait_calls==0,"active renderer defers source without waiting");
    replica_scene_idle_begin();
    check(replica_scene_try_begin((void*)1)==1,"source acquires only idle window");
    check(replica_scene_try_begin((void*)1)==0,"nested transaction cannot acquire");
    fake_thread=2;check(replica_scene_end((void*)1,1)==-1,"foreign source cannot end transaction");fake_thread=1;
    fake_time=3.25;check(replica_scene_end((void*)1,1)==1,"commit releases idle window");
    replica_scene_idle_end();check(replica_scene_gate.state==REPLICA_SCENE_RENDERING,"renderer owns next drawing phase");
    replica_scene_stats(stats);check(stats[0]==1 && stats[1]==2 && stats[2]==1 && stats[3]==3.25 && stats[4]==0,"transaction timing diagnostics");
    replica_scene_idle_begin();check(replica_scene_try_begin((void*)1)==1,"second transaction acquires");
    fake_thread=2;finish_on_sleep=true;replica_scene_idle_end();
    check(wait_calls==1 && replica_scene_gate.state==REPLICA_SCENE_RENDERING && !fatal_calls,"renderer wakes on committed idle mutation");
    replica_scene_stats(stats);check(stats[4]==1 && stats[5]==1,"render wait diagnostics");
    fake_thread=1;replica_scene_idle_begin();check(replica_scene_try_begin((void*)1)==1,"stalled transaction acquires");
    fake_thread=2;replica_scene_idle_end();replica_scene_stats(stats);
    check(fatal_calls==1 && stats[6]==1 && wait_calls==2 && replica_scene_gate.state==REPLICA_SCENE_MUTATING,"100ms timeout fails closed before another draw");
    fake_thread=1;check(replica_scene_end((void*)1,0)==-1 && fatal_calls==2,"failed import cannot publish a partial scene");
    check(replica_scene_gate.state==REPLICA_SCENE_MUTATING,"fatal paths never release render ownership");
    puts("Native scene gate: nonblocking source, owned commit, render-idle wait and bounded fail-closed timeout passed.");
    return 0;
}
