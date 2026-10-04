"""Desktop launcher for the experimental native multiplayer sessions."""
import argparse
from pathlib import Path
import json
import queue
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox,filedialog
from native_launcher import Launcher, discover
from native_paths import STATE_ROOT


class Desktop:
    def __init__(self,root,exe,local_only=False):
        self.root=root;self.host=Launcher(exe,local_only);self.join=Launcher(exe,local_only)
        self.events=queue.Queue();self.servers=[];self.closing=False
        root.title('Repopulated — Multiplayer');root.geometry('840x740');root.minsize(780,700)
        root.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',background='#18212b',foreground='#e8e3d3')
        style.configure('TEntry',fieldbackground='#edf0ed',foreground='#17212b')
        style.configure('TCombobox',fieldbackground='#edf0ed',foreground='#17212b')
        root.configure(background='#18212b')
        frame=ttk.Frame(root,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='REPOPULATED',font=('Segoe UI',22)).pack(anchor='w')
        ttk.Label(frame,text='Experimental multiplayer • stock ships • two players').pack(anchor='w',pady=(0,14))
        if local_only:ttk.Label(frame,text='Local test: accepts connections from this computer only.').pack(anchor='w')
        self.game=self.field(frame,'Reassembly executable',str(exe))
        ttk.Button(frame,text='Choose installation',command=self.choose_game).pack(anchor='w',pady=(0,10))
        tabs=ttk.Notebook(frame);tabs.pack(fill='both',expand=True)
        hostpane=ttk.Frame(tabs,padding=14);joinpane=ttk.Frame(tabs,padding=14)
        tabs.add(hostpane,text='Host');tabs.add(joinpane,text='Join / Server browser')
        self.name=self.field(hostpane,'Server name','Repopulated alpha')
        self.hostport=self.field(hostpane,'Game port','32916')
        self.saved_ids=[None];self.saved_choice=tk.StringVar(value='New galaxy');self.previous_checkpoint=None
        ttk.Label(hostpane,text='Galaxy').pack(anchor='w')
        self.saved_combo=ttk.Combobox(hostpane,textvariable=self.saved_choice,state='readonly');self.saved_combo.pack(fill='x',pady=5)
        self.shared_exploration=tk.BooleanVar(value=False)
        self.shared_checkbox=ttk.Checkbutton(hostpane,text='Share exploration between players',variable=self.shared_exploration)
        self.shared_checkbox.pack(anchor='w',pady=(2,4))
        self.saved_combo.bind('<<ComboboxSelected>>',self.select_saved_game)
        self.refresh_saves()
        ttk.Button(hostpane,text='Host game',command=lambda:self.start('host')).pack(anchor='w',pady=8)
        ttk.Button(hostpane,text='Save current galaxy',command=self.save).pack(anchor='w')
        self.hoststatus=tk.StringVar();ttk.Label(hostpane,textvariable=self.hoststatus,wraplength=650).pack(anchor='w',pady=8)
        self.hosttoken=tk.StringVar()
        ttk.Label(hostpane,text='Join token (share with your other player)').pack(anchor='w')
        ttk.Entry(hostpane,textvariable=self.hosttoken,state='readonly',width=75).pack(fill='x',pady=5)
        ttk.Button(hostpane,text='Copy join token',command=self.copy_token).pack(anchor='w')
        ttk.Button(hostpane,text='Stop hosting',command=self.host.shutdown).pack(anchor='w',pady=10)
        self.address=self.field(joinpane,'Host address','127.0.0.1')
        self.joinport=self.field(joinpane,'Game port','32916')
        self.token=self.field(joinpane,'Join token','')
        self.native_campaign=tk.BooleanVar(value=True)
        ttk.Checkbutton(joinpane,text='Native campaign controls and HUD',variable=self.native_campaign).pack(anchor='w')
        ttk.Label(joinpane,text='Initial rotation mode (the game handles subsequent mode changes)').pack(anchor='w')
        self.control_scheme=tk.StringVar(value='MOUSE_ROT')
        ttk.Combobox(joinpane,textvariable=self.control_scheme,values=('MOUSE_ROT','KEY_ROT','CARDINAL'),state='readonly').pack(fill='x',pady=(2,6))
        buttons=ttk.Frame(joinpane);buttons.pack(fill='x',pady=8)
        ttk.Button(buttons,text='Join game',command=lambda:self.start('join')).pack(side='left')
        ttk.Button(buttons,text='Stop joining',command=self.join.shutdown).pack(side='left',padx=8)
        ttk.Button(buttons,text='Refresh LAN servers',command=self.refresh).pack(side='left')
        self.listbox=tk.Listbox(joinpane,height=5,bg='#101820',fg='#e8e3d3',selectbackground='#685833')
        self.listbox.pack(fill='both',expand=True);self.listbox.bind('<<ListboxSelect>>',self.select)
        self.joinstatus=tk.StringVar();ttk.Label(joinpane,textvariable=self.joinstatus,wraplength=650).pack(anchor='w',pady=8)
        ttk.Label(frame,text='Alpha saves retain this two-player galaxy. Faction choice and native remote ship editing remain under development.',wraplength=700).pack(anchor='w',pady=(12,0))
        root.after(200,self.poll)

    @staticmethod
    def field(parent,label,value):
        ttk.Label(parent,text=label).pack(anchor='w')
        var=tk.StringVar(value=value);ttk.Entry(parent,textvariable=var).pack(fill='x',pady=(2,6));return var

    def start(self,mode):
        try:
            controller=self.host if mode=='host' else self.join
            exe=Path(self.game.get()).expanduser().resolve()
            if not exe.is_file():raise ValueError('Choose the installed Reassembly executable')
            controller.exe=exe
            request={'mode':mode,'port':int((self.hostport if mode=='host' else self.joinport).get()),
                     'address':self.address.get(),'token':self.token.get(),'name':self.name.get()}
            if mode=='host':request.update(checkpoint=self.saved_ids[max(0,self.saved_combo.current())],sharedExploration=self.shared_exploration.get())
            else:request.update(nativeCampaign=self.native_campaign.get(),controlScheme=self.control_scheme.get())
            controller.start(request)
        except (ValueError,OSError) as error:messagebox.showerror('Cannot start session',str(error),parent=self.root)

    def choose_game(self):
        path=filedialog.askopenfilename(parent=self.root,title='Choose ReassemblyRelease.exe',filetypes=[('Reassembly executable','ReassemblyRelease.exe'),('Executables','*.exe')])
        if path:self.game.set(path)

    def copy_token(self):
        if self.hosttoken.get():
            self.root.clipboard_clear();self.root.clipboard_append(self.hosttoken.get())
            self.token.set(self.hosttoken.get())

    def refresh_saves(self):
        saves=self.host.checkpoints.list();self.saved_ids=[None]+[s['id'] for s in saves]
        self.saved_combo['values']=['New galaxy']+[str(s['name'])+' — '+str(s['created'])[:19]+' ['+s['id'][:8]+']' for s in saves]
        self.saved_combo.current(0)

    def select_saved_game(self,event):
        from campaign_map_wire import load_map_settings
        ident=self.saved_ids[max(0,self.saved_combo.current())]
        try:self.shared_exploration.set(load_map_settings(self.host.checkpoints.root/ident/'slot')['sharedExploration'] if ident else False)
        except (ValueError,OSError):self.shared_exploration.set(False)

    def save(self):
        try:self.host.save()
        except ValueError as error:messagebox.showerror('Cannot save',str(error),parent=self.root)

    def refresh(self):
        def worker():
            try:self.events.put(('servers',discover()))
            except OSError as error:self.events.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def select(self,event):
        selected=self.listbox.curselection()
        if selected:
            server=self.servers[selected[0]];self.address.set(server['address']);self.joinport.set(str(server['port']))

    def poll(self):
        for controller,var in ((self.host,self.hoststatus),(self.join,self.joinstatus)):
            state=controller.status();text=state['state']
            if state.get('error'):text+=': '+state['error']
            if state.get('saveStatus'):text+=' • '+state['saveStatus']
            if state.get('notice'):text+=' • '+state['notice']
            if state.get('clientSnapshot'):
                text+=f" • snapshot {state['clientSnapshot']}"
                if state.get('clientFps') is not None:text+=f" • client {state['clientFps']} FPS"
                if state.get('lastReplicaUpdateAge',0)>2:text+=f" • last update {state['lastReplicaUpdateAge']}s ago"
            if state.get('campaignMap'):
                chart=state['campaignMap'];text+=f" • map {chart['explorationPercent']}% explored ({chart['exploration']}) • {chart['stations']} stations"
            if 'scenes' in state:text+=f" • snapshots {state['scenes']} • inputs {state['inputs']} • player {'connected' if state['remoteConnected'] else 'waiting'}"
            var.set(text)
            if controller is self.host:
                self.shared_checkbox.configure(state='disabled' if state['state'] in ('starting','hosting','stopping') else 'normal')
                self.hosttoken.set(state.get('token',''))
                if state.get('lastCheckpoint') and state['lastCheckpoint']!=self.previous_checkpoint:
                    self.previous_checkpoint=state['lastCheckpoint'];self.refresh_saves()
        while not self.events.empty():
            kind,data=self.events.get_nowait()
            if kind=='servers':
                self.servers=data;self.listbox.delete(0,'end')
                for server in data:self.listbox.insert('end',f"{server['name']} — {server['address']}:{server['port']} — {server['players']}/2"+(' [different build]' if not server['compatible'] else ''))
            else:self.joinstatus.set(data)
        if self.closing:
            if not any(c.worker and c.worker.is_alive() for c in (self.host,self.join)):
                self.root.destroy();return
        self.root.after(200,self.poll)

    def close(self):
        self.closing=True;self.host.shutdown();self.join.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,default=Path('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe'))
    parser.add_argument('--self-check',action='store_true');parser.add_argument('--native-smoke',action='store_true')
    parser.add_argument('--local-only',action='store_true',help='Bind only loopback and disable LAN advertisement for local tests')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if (args.self_check or args.native_smoke) and not args.report:parser.error('A report path is required for checks')
    if args.native_smoke:
        from native_coop import CoopHost,run_client
        from native_checkpoints import Checkpoints
        folder=STATE_ROOT/('packaged-check-'+str(time.time_ns()));folder.mkdir(parents=True);host=None
        try:
            host=CoopHost(args.exe,folder/'host',('127.0.0.1',0))
            result=run_client(args.exe,folder/'client',('127.0.0.1',host.server.server_address[1]),host.token,seconds=10,test_controls=True)
            checkpoint=host.checkpoint(Checkpoints(folder/'checkpoints'),'Packaged smoke check')
            result.update(hostFailures=host.failures,checkpoint=checkpoint['id'])
            if result['failures'] or host.failures or result['updates']<3:raise RuntimeError('Packaged native test failed')
            if any(result['presentation'].get(key,0)<10 for key in ('thrustEmissions','projectileDraws','turretsApplied')):
                raise RuntimeError('Packaged native presentation test failed')
            gate=result.get('sceneGate',{});threads=result.get('nativeThreads',{});pacing=result.get('framePacing',{})
            if not result.get('sceneIdleGate') or gate.get('transactions',0)<3 or gate.get('timeouts',0):
                raise RuntimeError('Packaged native scene synchronization did not advance safely')
            if (result.get('motionFramesApplied',0)<10 or not threads.get('nativeUpdateThread') or
                not threads.get('drawThread') or threads['drawThread']==threads['nativeUpdateThread']):
                raise RuntimeError('Packaged native motion or thread ownership check failed')
            if pacing.get('failures',0) or pacing.get('maximumRequestedMs',0)>25:
                raise RuntimeError('Packaged native frame pacing check failed')
            if ('requests' not in result.get('sceneHandoff',{}) or
                'longestActivePendingAgeMs' not in result.get('motionAdmission',{})):
                raise RuntimeError('Packaged native handoff DLL and admission diagnostics are incomplete')
            args.report.write_text(json.dumps(result,indent=2))
        except Exception as error:
            args.report.write_text(json.dumps({'error':str(error)}));raise
        finally:
            if host:host.close()
        return
    root=tk.Tk()
    if args.self_check:root.withdraw()
    desktop=Desktop(root,args.exe,args.local_only)
    if args.self_check:
        root.update_idletasks();args.report.write_text(json.dumps({'desktopConstructed':True,'host':desktop.host.status()['state'],'join':desktop.join.status()['state'],'requiredHeight':root.winfo_reqheight(),'sharedExplorationDefault':desktop.shared_exploration.get()}));root.destroy()
    else:root.mainloop()


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
