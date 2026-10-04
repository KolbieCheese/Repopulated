/* Local movement prediction for the verified native Player. The game's mover
 * helper supplies forces and its velocity integrator supplies damping/caps.
 * Collisions, weapons, damage and economy remain host-only. */
struct PredictionPose { unsigned tick;double state[4];float angle,angular;bool valid; };
static struct PredictionPose prediction_history[256];
static unsigned prediction_tick,prediction_ack,prediction_sequence;
static double prediction_received;
static bool prediction_enabled;
static double prediction_max_error;
static unsigned long long prediction_steps,prediction_reconciliations;
static void prediction_reset(void){memset(prediction_history,0,sizeof(prediction_history));}
static bool replica_is_predicted(unsigned ident){return prediction_enabled && replica_render_pilot && ident==0x70000002;}
static bool replica_prediction_active(uintptr_t cluster){return prediction_enabled && replica_render_pilot==cluster && prediction_received && replica_now_millis()-prediction_received<=250;}
__declspec(dllexport) void RepopulatedEnablePrediction(bool enabled){prediction_enabled=enabled;prediction_reset();}
__declspec(dllexport) unsigned RepopulatedPredictionTick(void){return prediction_tick;}
__declspec(dllexport) void RepopulatedPredictionAck(unsigned tick,unsigned seq){
    prediction_ack=tick;
    if(seq!=prediction_sequence){prediction_sequence=seq;prediction_received=replica_now_millis();}
}
static void prediction_reconcile(unsigned ident,float *position,float *velocity,float *angle,float *angular){
    if(!replica_is_predicted(ident) || !prediction_ack || prediction_ack>prediction_tick || prediction_tick-prediction_ack>15)return;
    struct PredictionPose *ack=&prediction_history[prediction_ack%256],*current=&prediction_history[prediction_tick%256];
    if(!ack->valid || !current->valid || ack->tick!=prediction_ack || current->tick!=prediction_tick)return;
    double host[4]={position[0],position[1],velocity[0],velocity[1]},delta[4];
    for(int i=0;i<4;i++)delta[i]=host[i]-ack->state[i];
    float da=remainderf(*angle-ack->angle,6.283185307f),dw=*angular-ack->angular;
    double error=hypot(delta[0],delta[1]);prediction_max_error=fmax(prediction_max_error,error);
    if(error>200 || fabsf(da)>2.0f){prediction_reset();return;}
    /* Rebase the retained local motion after the acknowledged input. This
     * preserves an immediate response without accumulating old corrections. */
    for(int i=0;i<256;i++){
        struct PredictionPose *p=&prediction_history[i];if(!p->valid || p->tick<prediction_ack || p->tick>prediction_tick)continue;
        for(int k=0;k<4;k++)p->state[k]+=delta[k];p->angle+=da;p->angular+=dw;
    }
    position[0]=(float)current->state[0];position[1]=(float)current->state[1];velocity[0]=(float)current->state[2];velocity[1]=(float)current->state[3];*angle=current->angle;*angular=current->angular;prediction_reconciliations++;
}
static void replica_predict_step(void *space,double dt){
    if(!space || !replica_prediction_active(replica_render_pilot) || !isfinite(dt) || dt<=0 || dt>0.05)return;
    uintptr_t cluster=replica_render_pilot,zone;int type;
    if(!read_mem((char*)space+0x58,&zone,8) || !zone || replica_find((void*)zone,0x70000002)!=cluster || !read_mem((void*)(cluster+0x10),&type,4) || type!=0)return;
    /* RVA 0x15c6b0 reads only these cpSpace fields. A private header prevents
     * any temporary mutation of the game's shared physics body array. */
    typedef void (*Integrate)(void*,double);static Integrate integrate;
    if(!integrate){
        uintptr_t address=(uintptr_t)GetModuleHandleW(NULL)+0x15c6b0;
        const unsigned char prefix[]={0x48,0x8b,0xc4,0x55,0x53,0x56,0x57,0x41,0x56,0x41,0x57};unsigned char actual[sizeof(prefix)];
        if(!read_mem((void*)address,actual,sizeof(actual)) || memcmp(actual,prefix,sizeof(prefix)))return;
        memcpy(&integrate,&address,8);
    }
    double state[4];float angle,angular;
    if(!read_mem((void*)(cluster+0x30),state,32) || !read_mem((void*)(cluster+0x60),&angle,4) || !read_mem((void*)(cluster+0x64),&angular,4))return;
    for(int i=0;i<4;i++)if(!isfinite(state[i]))return;
    if(!isfinite(angle) || !isfinite(angular))return;
    /* Native cpSpaceStep advances position before its velocity update. */
    state[0]+=state[2]*dt;state[1]+=state[3]*dt;angle=(float)((double)angle+(double)angular*dt);
    memcpy((void*)(cluster+0x30),state,16);memcpy((void*)(cluster+0x60),&angle,4);
    double orientation[2]={cos((double)angle),sin((double)angle)};memcpy((void*)(cluster+0x70),orientation,16);
    unsigned char header[0x80];if(!read_mem(space,header,sizeof(header)))return;
    uintptr_t body=cluster+0x10;struct {int count,capacity;uintptr_t *items;} bodies={1,1,&body};void *array=&bodies;memcpy(header+0x78,&array,8);
    integrate(header,dt);
    double zero[2]={0};float torque=0;memcpy((void*)(cluster+0x50),zero,16);memcpy((void*)(cluster+0x68),&torque,4);
    struct PredictionPose *p=&prediction_history[(++prediction_tick)%256];p->tick=prediction_tick;p->valid=true;
    memcpy(p->state,(void*)(cluster+0x30),32);memcpy(&p->angle,(void*)(cluster+0x60),4);memcpy(&p->angular,(void*)(cluster+0x64),4);prediction_steps++;
}
__declspec(dllexport) void RepopulatedPredictionStats(double *out){out[0]=(double)prediction_steps;out[1]=(double)prediction_reconciliations;out[2]=prediction_max_error;out[3]=(double)prediction_tick;}
