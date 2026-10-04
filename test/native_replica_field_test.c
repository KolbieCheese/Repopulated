#include <assert.h>
#include <stdio.h>
#include "../native/replica_field.h"
static union ReplicaFieldConsole console,other;
static union ReplicaFieldZone zone;
static struct ReplicaFieldContext context;
static int calls;
static uintptr_t field=0x12345;
static bool readable=true;
static bool reader(const void *p,void *out,size_t size){if(!readable)return false;memcpy(out,p,size);return true;}
static void *getter(void *arg){
    calls++;
    if(arg==other.bytes)return (void *)0x777;
    assert(arg==context.shadow_console.bytes);
    uintptr_t owner,streamer,previous;float position[2];
    memcpy(&owner,(char *)arg+8,8);memcpy(&streamer,(void *)(owner+0x248),8);
    assert(owner==(uintptr_t)context.shadow_zone.bytes && !streamer);
    memcpy(position,(char *)arg+0xb8,8);assert(position[0]==6001 && position[1]==-6002);
    memcpy(&previous,(char *)arg+0x2f8,8);assert(previous==0 || previous==field);
    memcpy((char *)arg+0x2f8,&field,8);return (void *)field;
}
int main(void){
    uintptr_t real=(uintptr_t)zone.bytes,streamer=0x98765,campaign_field=0x999;
    float position[2]={6001,-6002};
    memcpy(console.bytes+8,&real,8);memcpy(console.bytes+0xb8,position,8);
    memcpy(console.bytes+0x2f8,&campaign_field,8);memcpy(zone.bytes+0x248,&streamer,8);
    unsigned char before_console[0x300],before_zone[0x250];
    memcpy(before_console,console.bytes,sizeof(before_console));memcpy(before_zone,zone.bytes,sizeof(before_zone));
    assert(replica_field_configure(&context,console.bytes,zone.bytes,42,reader)==1);
    assert(replica_field_get(&context,console.bytes,42,reader,getter)==(void *)field);
    assert(replica_field_get(&context,console.bytes,42,reader,getter)==(void *)field);
    assert(!memcmp(before_console,console.bytes,sizeof(before_console)) && !memcmp(before_zone,zone.bytes,sizeof(before_zone)));
    assert(replica_field_get(&context,other.bytes,99,reader,getter)==(void *)0x777);
    assert(replica_field_configure(&context,console.bytes,zone.bytes,42,reader)==1);
    assert(replica_field_configure(&context,console.bytes,zone.bytes,99,reader)==-2);
    assert(!replica_field_get(&context,console.bytes,99,reader,getter));
    readable=false;assert(!replica_field_get(&context,console.bytes,42,reader,getter));readable=true;
    uintptr_t invalid=0;memcpy(console.bytes+8,&invalid,8);
    assert(!replica_field_get(&context,console.bytes,42,reader,getter));memcpy(console.bytes+8,&real,8);
    position[0]=NAN;memcpy(console.bytes+0xb8,position,8);
    assert(!replica_field_get(&context,console.bytes,42,reader,getter));
    assert(calls==3 && context.private_calls==2 && context.forwarded_calls==1 && context.rejected_calls==4);
    puts("Private field isolation, retained native field, forwarding, owner/thread and finite-position guards passed");
}
