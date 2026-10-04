/* Bounded conversion of owned wire buffers. No game objects or native locks. */
#ifndef REPOPULATED_WIRE_HEX_H
#define REPOPULATED_WIRE_HEX_H
#include <stddef.h>
#include <stdint.h>

#define REPLICA_HEX_RAW_LIMIT (1024*1024)
static int replica_hex_nibble(unsigned char value){
    if(value>='0' && value<='9')return value-'0';
    if(value>='a' && value<='f')return value-'a'+10;
    return -1;
}
static int replica_hex_overlap(const void *input,size_t input_size,const void *output,size_t output_size){
    uintptr_t a=(uintptr_t)input,b=(uintptr_t)output;
    if(a>UINTPTR_MAX-input_size || b>UINTPTR_MAX-output_size)return -1;
    return input_size && output_size && a<b+output_size && b<a+input_size;
}
static int replica_hex_decode(const char *hex,int length,unsigned char *out,int capacity){
    if(length<0 || length>REPLICA_HEX_RAW_LIMIT*2 || length%2 || capacity<0 ||
       capacity>REPLICA_HEX_RAW_LIMIT || length/2>capacity || (length && (!hex || !out)))return -1;
    int overlap=replica_hex_overlap(hex,(size_t)length,out,(size_t)length/2);
    if(overlap)return overlap<0?-1:-3;
    /* Check the entire payload before changing any output byte. */
    for(int i=0;i<length;i++)if(replica_hex_nibble((unsigned char)hex[i])<0)return -2;
    for(int i=0;i<length;i+=2)out[i/2]=(unsigned char)((replica_hex_nibble((unsigned char)hex[i])<<4)|
                                                                   replica_hex_nibble((unsigned char)hex[i+1]));
    return length/2;
}
static int replica_hex_encode(const unsigned char *bytes,int length,char *out,int capacity){
    static const char digits[]="0123456789abcdef";
    if(length<0 || length>REPLICA_HEX_RAW_LIMIT || capacity<1 || capacity>REPLICA_HEX_RAW_LIMIT*2+1 ||
       capacity<length*2+1 || !out || (length && !bytes))return -1;
    int overlap=replica_hex_overlap(bytes,(size_t)length,out,(size_t)length*2+1);
    if(overlap)return overlap<0?-1:-3;
    for(int i=0;i<length;i++){out[i*2]=digits[bytes[i]>>4];out[i*2+1]=digits[bytes[i]&15];}
    out[length*2]=0;return length*2;
}
#endif
