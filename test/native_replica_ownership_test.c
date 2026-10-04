#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
static unsigned char serial[32];
static bool unreadable;
static bool read_mem(const void *source,void *out,size_t size){
    uintptr_t base=(uintptr_t)serial,address=(uintptr_t)source;
    if(unreadable || address<base || address-base>sizeof(serial) || size>sizeof(serial)-(address-base))return false;
    memcpy(out,source,size);return true;
}
#include "../native/replica_ownership.h"
int main(void){
    unsigned ident=0x7000005d;int faction=8,result=-999,native_cache=0;
    memcpy(serial+8,&ident,4);memcpy(serial+0x10,&faction,4);
    assert(replica_serial_owner((uintptr_t)serial,ident,&result) && result==8);
    assert(native_cache==0); /* Reading canonical ownership never changes game allegiance. */
    assert(result!=native_cache); /* An owned command keeps faction8 even if cached native faction is0. */
    assert(replica_commandless_owner()==0); /* Native commandless cache8 has no serialized owner. */
    native_cache=8;assert(replica_commandless_owner()!=native_cache && native_cache==8);
    faction=20008;memcpy(serial+0x10,&faction,4);
    assert(replica_serial_owner((uintptr_t)serial,ident,&result) && result==20008);
    assert(result!=8); /* A changed owned-command faction must still fail an old faction8 guard. */
    result=-999;assert(!replica_serial_owner((uintptr_t)serial,ident+1,&result) && result==-999);
    assert(!replica_serial_owner(0,ident,&result));assert(!replica_serial_owner((uintptr_t)serial,0,&result));
    assert(!replica_serial_owner((uintptr_t)serial,ident,NULL));
    faction=-1;memcpy(serial+0x10,&faction,4);assert(!replica_serial_owner((uintptr_t)serial,ident,&result));
    faction=0;memcpy(serial+0x10,&faction,4);assert(replica_serial_owner((uintptr_t)serial,ident,&result) && result==0);
    unreadable=true;assert(!replica_serial_owner((uintptr_t)serial,ident,&result));
    puts("replica ownership: serialized command faction, neutral fragments and strict changed-owner/identity rejection passed");
    return 0;
}
