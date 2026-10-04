/* Validate the production read-only focus helper against membership/lifetime
 * changes, without starting the game or touching a real native object. */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <assert.h>
static unsigned char zone[0x198],clusters[3][0x180];
static uintptr_t roots[3];
static int transition;
static void putptr(void *base,size_t offset,uintptr_t value){memcpy((char*)base+offset,&value,sizeof(value));}
static void putid(void *base,unsigned value){memcpy((char*)base+0x100,&value,sizeof(value));}
static bool within(const void *source,size_t size,const void *base,size_t length){
    uintptr_t address=(uintptr_t)source,begin=(uintptr_t)base;
    return address>=begin && address-begin<=length && size<=length-(address-begin);
}
static bool read_mem(const void *source,void *out,size_t size){
    if(!within(source,size,zone,sizeof(zone)) && !within(source,size,clusters,sizeof(clusters)) &&
       !within(source,size,roots,sizeof(roots)))return false;
    memcpy(out,source,size);
    if(source==clusters[0]+0xd8){
        if(transition==1)putid(clusters[0],0x70000001u);
        if(transition==2)putptr(clusters[0],8,0);
        if(transition==3)putptr(clusters[0],0x178,(uintptr_t)clusters[2]);
        if(transition==4)return false;
    }
    return true;
}
static unsigned RepopulatedClusterIdent(void *cluster){
    unsigned ident=0;return read_mem((char*)cluster+0x100,&ident,sizeof(ident))?ident:0;
}
#include "../native/comparison_focus.h"
static void reset(void){
    memset(zone,0,sizeof(zone));memset(clusters,0,sizeof(clusters));transition=0;
    for(int i=0;i<3;i++){roots[i]=(uintptr_t)clusters[i];putptr(clusters[i],8,(uintptr_t)zone);putid(clusters[i],0x70000001u+(unsigned)i);}
    putid(clusters[0],0x70000002u);putid(clusters[1],0x12345678u);
    float point[2]={3500.125f,2999.875f};memcpy(clusters[0]+0xd8,point,sizeof(point));
    putptr(zone,0x188,(uintptr_t)roots);putptr(zone,0x190,(uintptr_t)(roots+2));
}
static void rejected(int expected){
    double out[2]={-91,-92};assert(replica_comparison_focus(zone,0x70000002u,out)==expected);
    assert(out[0]==-91 && out[1]==-92);
}
int main(void){
    double out[2];reset();assert(replica_comparison_focus(zone,0x70000002u,out)==1);
    assert(out[0]==3500.125 && out[1]==2999.875); /* World coords, with local sector offset. */
    assert(replica_comparison_focus(NULL,0x70000002u,out)==-1);
    assert(replica_comparison_focus(zone,9,out)==-1);
    assert(replica_comparison_focus(zone,0x70000002u,NULL)==-1);
    reset();putid(clusters[1],0x70000002u);rejected(-2);
    reset();putid(clusters[0],0x70000001u);rejected(0); /* Reused pointer in same zone. */
    reset();putptr(clusters[0],8,0);rejected(0);
    reset();putptr(clusters[0],0x178,(uintptr_t)clusters[2]);rejected(0);
    reset();float invalid=NAN;memcpy(clusters[0]+0xd8,&invalid,sizeof(invalid));rejected(0);
    reset();invalid=2e9f;memcpy(clusters[0]+0xd8,&invalid,sizeof(invalid));rejected(0);
    reset();putptr(zone,0x190,(uintptr_t)roots-8);rejected(0);
    reset();putptr(zone,0x190,(uintptr_t)roots+1);rejected(0);
    reset();putptr(zone,0x190,(uintptr_t)roots+4097*8);rejected(0);
    reset();roots[1]=0x12345678u;rejected(0); /* Unreadable vector member. */
    for(int change=1;change<=4;change++){reset();transition=change;rejected(0);}
    puts("comparison focus: current world point, ambiguous IDs, finite bounds and lifetime transitions passed");
    return 0;
}
