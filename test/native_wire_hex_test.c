#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../native/wire_hex.h"

int main(void){
    unsigned char bytes[256],decoded[256];char encoded[513];
    for(int i=0;i<256;i++)bytes[i]=(unsigned char)i;
    assert(replica_hex_encode(bytes,256,encoded,sizeof(encoded))==512);
    assert(!strcmp(encoded+480,"f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"));
    assert(replica_hex_decode(encoded,512,decoded,sizeof(decoded))==256);
    assert(!memcmp(bytes,decoded,sizeof(bytes)));
    for(int bad=0;bad<512;bad++){
        char old=encoded[bad];encoded[bad]='G';memset(decoded,0x77,sizeof(decoded));
        assert(replica_hex_decode(encoded,512,decoded,sizeof(decoded))==-2);
        for(int i=0;i<256;i++)assert(decoded[i]==0x77);
        encoded[bad]=old;
    }
    assert(replica_hex_decode("ABCDEF",6,decoded,256)==-2);
    assert(replica_hex_decode("00 0",4,decoded,256)==-2);
    assert(replica_hex_decode(NULL,0,NULL,0)==0);
    assert(replica_hex_encode(NULL,0,encoded,1)==0 && encoded[0]==0);
    memset(decoded,0x77,256);memset(encoded,'X',513);
    assert(replica_hex_decode("00",1,decoded,256)==-1);
    assert(replica_hex_decode("00",2,decoded,0)==-1);
    assert(replica_hex_decode(NULL,2,decoded,256)==-1);
    assert(replica_hex_decode("00",2,NULL,256)==-1);
    assert(replica_hex_decode("00",2,decoded,-1)==-1);
    assert(replica_hex_encode(bytes,256,encoded,512)==-1);
    assert(replica_hex_encode(NULL,1,encoded,513)==-1);
    assert(replica_hex_encode(bytes,1,NULL,513)==-1);
    assert(replica_hex_encode(bytes,-1,encoded,513)==-1);
    assert(decoded[0]==0x77 && encoded[0]=='X');
    unsigned char overlap[16]={0};
    memcpy(overlap,"0000",4);
    assert(replica_hex_decode((char*)overlap,4,overlap,16)==-3);
    assert(replica_hex_encode(overlap,2,(char*)overlap,16)==-3);
    assert(replica_hex_overlap((void*)(UINTPTR_MAX-1),4,(void*)16,1)==-1);
    unsigned char *large=malloc(REPLICA_HEX_RAW_LIMIT),*copy=malloc(REPLICA_HEX_RAW_LIMIT);
    char *hex=malloc(REPLICA_HEX_RAW_LIMIT*2+1);assert(large && copy && hex);
    for(int i=0;i<REPLICA_HEX_RAW_LIMIT;i++)large[i]=(unsigned char)(i*73+19);
    assert(replica_hex_encode(large,REPLICA_HEX_RAW_LIMIT,hex,REPLICA_HEX_RAW_LIMIT*2+1)==REPLICA_HEX_RAW_LIMIT*2);
    assert(replica_hex_decode(hex,REPLICA_HEX_RAW_LIMIT*2,copy,REPLICA_HEX_RAW_LIMIT)==REPLICA_HEX_RAW_LIMIT);
    assert(!memcmp(large,copy,REPLICA_HEX_RAW_LIMIT));
    assert(replica_hex_encode(large,REPLICA_HEX_RAW_LIMIT+1,hex,REPLICA_HEX_RAW_LIMIT*2+1)==-1);
    assert(replica_hex_decode(hex,REPLICA_HEX_RAW_LIMIT*2+2,copy,REPLICA_HEX_RAW_LIMIT)==-1);
    free(large);free(copy);free(hex);
    puts("Wire hex exact round trips, rejection-before-write and maximum payload passed");return 0;
}
