/* Exercise production routing/copy/publication code with bounded fake native
 * objects. Exact game entry signatures still require live checks. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static bool compatible=true;
static void initialize(void){}
static unsigned original_calls,owned_calls;
static bool force_seen,remote_enabled=true;
static void original(void *ai,bool force){(void)ai;original_calls++;force_seen=force;}
static void owned(void *ai,bool force){owned_calls++;if(!remote_enabled)original(ai,force);}
#include "../native/host_ai.c"
static unsigned char ai[0x280],command[0xc0],cluster[0x120],serial[0x20];
static void set_objects(int faction,unsigned pilot){
    uintptr_t p=(uintptr_t)command;memcpy(ai+0x278,&p,8);
    p=(uintptr_t)cluster;memcpy(command+0xb8,&p,8);
    p=(uintptr_t)serial;memcpy(command+0x28,&p,8);
    memcpy(cluster+0x118,&faction,4);memcpy(serial+8,&pilot,4);
}
static void require(bool value,const char *name){if(!value){fprintf(stderr,"%s\n",name);exit(1);}}
int main(void){
    struct HostAIOwner rows[]={{20008,0x70000002},{100,0}};
    require(RepopulatedConfigureHostAI(rows,(void*)owned,rows,2)==-2,"reject non-executable original pointer");
    require(RepopulatedConfigureHostAI((void*)original,(void*)owned,rows,2)==0,"configure exact routing");
    rows[0].faction=5;rows[0].pilot=123; /* Config owns its immutable copy. */
    set_objects(20008,0x70000002);RepopulatedHostAIUpdate(ai,true);
    require(owned_calls==1 && original_calls==0,"owned exact pilot crosses callback");
    set_objects(20008,0x70000003);RepopulatedHostAIUpdate(ai,false);
    require(owned_calls==1 && original_calls==1 && !force_seen,"same faction other pilot remains native");
    set_objects(3,0x70000002);RepopulatedHostAIUpdate(ai,true);
    require(original_calls==2 && force_seen,"other faction same ident remains native");
    set_objects(100,123);RepopulatedHostAIUpdate(ai,false);
    require(owned_calls==2,"pilot zero owns any pilot in its faction");
    set_objects(20008,0x70000002);remote_enabled=false;RepopulatedHostAIUpdate(ai,true);
    require(owned_calls==3 && original_calls==3 && force_seen,"vacant owned seat callback preserves native AI");
    memset(ai,0,sizeof(ai));RepopulatedHostAIUpdate(ai,false);
    require(original_calls==4,"missing native command still forwards original");
    double stats[4];RepopulatedReadHostAIStats(stats);
    require(stats[0]==6 && stats[1]==3 && stats[2]==3 && stats[3]==1,"routing counters account for native and owned paths");
    require(RepopulatedConfigureHostAI((void*)original,(void*)owned,rows,2)==-4,"reject unsafe reconfiguration");
    puts("Native AI routing: ownership, native forwarding, vacant fallback and immutable config passed.");
    return 0;
}
