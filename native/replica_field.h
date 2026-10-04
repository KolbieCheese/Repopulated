/* The exact-build Console field getter reads only +8, +b8/+bc and +2f8.
 * A private console uses a private field without hiding the live campaign
 * streamer from the render thread. No real console/zone memory is written.
 * Evidence: verified win64 getter RVA cab60 and insert RVA 133290. */
#ifndef REPOPULATED_REPLICA_FIELD_H
#define REPOPULATED_REPLICA_FIELD_H
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <math.h>
typedef bool (*ReplicaFieldRead)(const void *,void *,size_t);
typedef void *(*ReplicaFieldGetter)(void *);
union ReplicaFieldConsole { uintptr_t alignment;unsigned char bytes[0x300]; };
union ReplicaFieldZone { uintptr_t alignment;unsigned char bytes[0x250]; };
struct ReplicaFieldContext {
    _Atomic uintptr_t console;uintptr_t zone;unsigned long thread;
    union ReplicaFieldConsole shadow_console;
    union ReplicaFieldZone shadow_zone;
    _Atomic unsigned long long private_calls,forwarded_calls,rejected_calls;
};
static int replica_field_configure(struct ReplicaFieldContext *ctx,void *console,void *zone,
                                  unsigned long thread,ReplicaFieldRead read) {
    uintptr_t owner=0;
    if(!ctx || !console || !zone || !thread || !read ||
       !read((char *)console+8,&owner,8) || owner!=(uintptr_t)zone)return -1;
    /* One immutable private console per process. Reconfiguration must not
     * discard the native field owned by the shadow console. */
    uintptr_t configured=atomic_load_explicit(&ctx->console,memory_order_acquire);
    if(configured)return configured==(uintptr_t)console && ctx->zone==(uintptr_t)zone && ctx->thread==thread?1:-2;
    memset(&ctx->shadow_console,0,sizeof(ctx->shadow_console));memset(&ctx->shadow_zone,0,sizeof(ctx->shadow_zone));
    ctx->zone=(uintptr_t)zone;ctx->thread=thread;
    uintptr_t shadow=(uintptr_t)ctx->shadow_zone.bytes;
    memcpy(ctx->shadow_console.bytes+8,&shadow,8);
    atomic_store_explicit(&ctx->console,(uintptr_t)console,memory_order_release);
    return 1;
}
static void *replica_field_get(struct ReplicaFieldContext *ctx,void *console,unsigned long thread,
                               ReplicaFieldRead read,ReplicaFieldGetter original) {
    if(!ctx || !original)return NULL;
    uintptr_t configured=atomic_load_explicit(&ctx->console,memory_order_acquire);
    if(!configured || (uintptr_t)console!=configured){atomic_fetch_add_explicit(&ctx->forwarded_calls,1,memory_order_relaxed);return original(console);}
    uintptr_t owner=0;float position[2];
    if(thread!=ctx->thread || !read || !read((char *)console+8,&owner,8) || owner!=ctx->zone ||
       !read((char *)console+0xb8,position,8) || !isfinite(position[0]) || !isfinite(position[1])){
        atomic_fetch_add_explicit(&ctx->rejected_calls,1,memory_order_relaxed);return NULL;
    }
    /* The original getter creates/frees its native field at shadow +2f8 and
     * moves that field between sectors. Its native lifetime stays independent
     * of every campaign field; the live zone's streamer is always untouched. */
    memcpy(ctx->shadow_console.bytes+0xb8,position,8);atomic_fetch_add_explicit(&ctx->private_calls,1,memory_order_relaxed);
    return original(ctx->shadow_console.bytes);
}
#endif
