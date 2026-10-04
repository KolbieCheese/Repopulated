"""Desktop launcher and packaged backend entry point."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent

def atomic_json(path,value):
    temporary=path.with_suffix(path.suffix+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)

class Launcher:
    BG='#171c24'; PANEL='#222a35'; TEXT='#e8e9e6'; MUTED='#a1abb9'; GOLD='#d3b66c'
    def __init__(self,root):
        import launch
        self.backend=launch;self.root=root;self.process=None;self.log_handle=None;self.test_session=False;self.test_steam=False;self.last_exit=None
        self.settings=json.loads((ROOT/'settings.json').read_text())
        preferences=ROOT/'launcher-settings.json'
        self.preferences=json.loads(preferences.read_text()) if preferences.exists() else {}
        self.game=tk.StringVar(value=self.preferences.get('gamePath',str(launch.find_game() or '')))
        self.status=tk.StringVar(value='Select your Reassembly installation.')
        self.center=tk.BooleanVar(value=self.settings.get('toolbarX') is None)
        self.show=tk.BooleanVar(value=self.settings.get('toolbarVisible',True))
        self.scale=tk.DoubleVar(value=self.settings.get('toolbarScale',1)*100)
        self.sort=tk.StringVar(value=self.settings.get('defaultSort','Default'))
        self.direction=tk.StringVar(value=self.settings.get('sortDirection','desc'))
        root.title('Reassembly • Build Menu Filters');root.geometry('960x760');root.minsize(820,720);root.configure(bg=self.BG)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',background=self.PANEL,foreground=self.TEXT,font=('Segoe UI',11))
        style.configure('TFrame',background=self.BG)
        style.configure('TLabel',background=self.BG,foreground=self.TEXT)
        style.configure('TButton',padding=(16,10),background='#303b49',borderwidth=0)
        style.map('TButton',background=[('active','#435064')],foreground=[('disabled','#79818d')])
        style.configure('Play.TButton',background=self.GOLD,foreground='#171c24',font=('Segoe UI Semibold',14),padding=(32,13))
        style.map('Play.TButton',background=[('active','#e5cc8d'),('disabled','#5f5745')],foreground=[('disabled','#b8b0a0')])
        style.configure('TEntry',fieldbackground='#303947',foreground=self.TEXT,insertcolor=self.TEXT,padding=8)
        style.configure('TCombobox',fieldbackground='#303947',foreground=self.TEXT,padding=7)
        style.map('TCombobox',fieldbackground=[('readonly','#303947')],foreground=[('readonly',self.TEXT)])
        style.configure('TCheckbutton',background=self.BG,foreground=self.TEXT,padding=8)
        root.option_add('*TCombobox*Listbox.background','#303947');root.option_add('*TCombobox*Listbox.foreground',self.TEXT)
        sidebar=tk.Frame(root,bg='#10151c',width=190);sidebar.pack(side='left',fill='y');sidebar.pack_propagate(False)
        tk.Label(sidebar,text='REASSEMBLY',fg=self.GOLD,bg='#10151c',font=('Segoe UI Semibold',15),pady=32).pack()
        tk.Label(sidebar,text='BUILD MENU\nFILTERS',fg=self.MUTED,bg='#10151c',font=('Segoe UI',10),justify='left').pack(pady=(0,30))
        self.pages={};self.nav={};self.content=ttk.Frame(root,padding=30);self.content.pack(side='left',fill='both',expand=True)
        for name in ('Play','Interface','Help'):
            button=tk.Button(sidebar,text=name,anchor='w',padx=24,pady=13,bd=0,bg='#10151c',fg=self.TEXT,
                             activebackground='#293341',activeforeground=self.GOLD,font=('Segoe UI',12),command=lambda n=name:self.page(n))
            button.pack(fill='x',padx=8,pady=3);self.nav[name]=button
            self.pages[name]=ttk.Frame(self.content)
        tk.Label(sidebar,text='Standalone extension\nVanilla + modded parts',bg='#10151c',fg=self.MUTED,font=('Segoe UI',9),justify='left').pack(side='bottom',pady=25)
        self.make_play();self.make_interface();self.make_help();self.page('Play')
        self.game.trace_add('write',lambda *_:self.validate())
        self.validate();root.after(500,self.poll)
        root.protocol('WM_DELETE_WINDOW',root.destroy) # Backend keeps its own lifetime; closing launcher does not kill the game.

    def label(self,parent,text,size=11,color=None):
        widget=ttk.Label(parent,text=text,font=('Segoe UI Semibold' if size>=18 else 'Segoe UI',size),foreground=color or self.TEXT,wraplength=630,justify='left')
        widget.pack(anchor='w',pady=(0,14));return widget

    def page(self,name):
        for n,p in self.pages.items():p.pack_forget();self.nav[n].configure(bg='#293341' if n==name else '#10151c',fg=self.GOLD if n==name else self.TEXT)
        self.pages[name].pack(fill='both',expand=True)

    def make_play(self):
        p=self.pages['Play'];self.label(p,'Build better. Find parts faster.',25)
        self.label(p,'Filter and sort your ship-building palette without changing your available technology.',11,self.MUTED)
        hero=tk.Canvas(p,height=180,bg=self.PANEL,highlightthickness=0);hero.pack(fill='x',pady=(6,24))
        for x,y,size in [(42,42,84),(145,42,84),(248,42,84)]:
            hero.create_rectangle(x,y,x+size,y+size,outline=self.GOLD,width=2)
            hero.create_line(x+15,y+size/2,x+size-15,y+size/2,fill='#8796a9',width=2)
        hero.create_text(42,153,text='CATEGORY   •   SEARCH   •   SOURCE   •   SORT',fill=self.TEXT,anchor='w',font=('Segoe UI',11))
        self.label(p,'Game installation',13)
        row=ttk.Frame(p);row.pack(fill='x');ttk.Entry(row,textvariable=self.game).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Browse…',command=self.browse).pack(side='left',padx=(8,0))
        self.status_label=ttk.Label(p,textvariable=self.status,wraplength=620,foreground=self.MUTED);self.status_label.pack(anchor='w',pady=(14,18))
        self.play=ttk.Button(p,text='PLAY REASSEMBLY',style='Play.TButton',command=self.start);self.play.pack(anchor='w')
        self.label(p,'F7: show / hide   •   Drag the header to move   •   Drag the corner to resize',10,self.MUTED).pack_configure(pady=(16,10))
        ttk.Button(p,text='View session log',command=self.view_log).pack(anchor='w',pady=8)

    def make_interface(self):
        p=self.pages['Interface'];self.label(p,'Make the builder yours.',25)
        self.label(p,'Changes here apply the next time you launch the game. In-game movement and resizing are saved automatically.',11,self.MUTED)
        ttk.Checkbutton(p,text='Start centered at the top',variable=self.center).pack(anchor='w',pady=6)
        ttk.Checkbutton(p,text='Show the toolbar when entering the builder',variable=self.show).pack(anchor='w',pady=6)
        self.label(p,'Interface size',13)
        row=ttk.Frame(p);row.pack(fill='x',pady=(0,20))
        self.scale_label=ttk.Label(row,width=6);self.scale_label.pack(side='right')
        ttk.Scale(row,from_=65,to=160,variable=self.scale,command=lambda v:self.scale_label.configure(text=f'{float(v):.0f}%')).pack(side='left',fill='x',expand=True)
        self.scale_label.configure(text=f'{self.scale.get():.0f}%')
        self.label(p,'Initial sort',13)
        self.sort_labels={'Game order':'Default','Name':'name','Mass':'mass','Health':'health','Cost (P)':'cost','Size (area)':'area','Power generation':'generation','Power storage':'powerStorage','Resource storage':'resources','Thrust':'thrust','Shield health':'shieldHealth','Weapon DPS':'dps','Weapon range':'range','Build time':'buildTime'}
        self.sort_choice=tk.StringVar(value=next((k for k,v in self.sort_labels.items() if v==self.sort.get()),'Game order'))
        ttk.Combobox(p,textvariable=self.sort_choice,values=list(self.sort_labels),state='readonly').pack(fill='x',pady=(0,12))
        ttk.Combobox(p,textvariable=self.direction,values=['desc','asc'],state='readonly').pack(fill='x')
        self.label(p,'desc = highest first   •   asc = lowest first',10,self.MUTED)
        buttons=ttk.Frame(p);buttons.pack(fill='x',pady=18)
        ttk.Button(buttons,text='Save preferences',command=self.save).pack(side='left')
        ttk.Button(buttons,text='Reset layout',command=self.reset_layout).pack(side='left',padx=10)
        self.saved=ttk.Label(p,text='',foreground=self.GOLD);self.saved.pack(anchor='w')

    def make_help(self):
        p=self.pages['Help'];self.label(p,'Ready for your next build.',25)
        self.label(p,'Play launches Reassembly with this extension enabled. Start the game through this launcher each time you want filters. Your existing Steam mods and saves remain available.',11,self.MUTED)
        for title,body in [
            ('Find parts','Click categories, search by name or ID, and cycle source factions. Reset restores all parts and the game’s original order.'),
            ('Move and resize','Drag the header; drag the bottom-right // grip to change size. Double-click the header to restore the centered default.'),
            ('Keyboard shortcuts','F6: category • Shift+F6: source • Ctrl+F: search\nF7: show/hide • F8: sort • Shift+F8: reverse'),
            ('Compatibility','Supports the verified Windows x64 game build. Unsupported executables are rejected before injection. Multiplayer coexistence has not yet been tested.')]:
            self.label(p,title,13);self.label(p,body,11,self.MUTED)
        ttk.Button(p,text='Open packaged README',command=lambda:os.startfile(ROOT/('README.txt' if (ROOT/'README.txt').exists() else 'README.md'))).pack(anchor='w')

    def browse(self):
        path=filedialog.askopenfilename(title='Select ReassemblyRelease.exe',filetypes=[('Reassembly executable','ReassemblyRelease.exe'),('Windows executable','*.exe')])
        if path:self.game.set(path)

    def validate(self):
        path=Path(self.game.get());valid=False
        try:
            if path.is_file():
                valid=hashlib.sha256(path.read_bytes()).hexdigest()==self.backend.KNOWN
                self.status.set('Compatible game found. Ready to play.' if valid else 'This game build is not supported yet. See Help for compatibility.')
            else:self.status.set('Select ReassemblyRelease.exe to get started.')
        except OSError:self.status.set('Unable to read that game executable.')
        self.play.configure(state='normal' if valid and self.process is None else 'disabled');return valid

    def save(self):
        settings=json.loads((ROOT/'settings.json').read_text())
        settings.update(toolbarVisible=self.show.get(),toolbarScale=round(self.scale.get()/100,2),defaultSort=self.sort_labels[self.sort_choice.get()],sortDirection=self.direction.get())
        if self.center.get():settings.update(toolbarX=None,toolbarY=12)
        atomic_json(ROOT/'settings.json',settings)
        atomic_json(ROOT/'launcher-settings.json',{'gamePath':self.game.get()})
        self.saved.configure(text='Preferences saved.');self.settings=settings

    def reset_layout(self):
        self.center.set(True);self.scale.set(100);self.scale_label.configure(text='100%');self.save()

    def start(self):
        if self.process is not None or not self.validate():return
        import frida
        if not self.test_session and any(p.name.lower()=='reassemblyrelease.exe' for p in frida.get_local_device().enumerate_processes()):
            self.status.set('Close the running Reassembly game before launching a new session.');return
        atomic_json(ROOT/'launcher-settings.json',{'gamePath':self.game.get()})
        args=([sys.executable,'--backend'] if getattr(sys,'frozen',False) else [sys.executable,str(ROOT/'launch.py')])+['--background','--exe',self.game.get()]
        if self.test_session:args+=['--test','--no-provenance']
        if self.test_steam:args+=['--steam-test']
        self.log_handle=open(ROOT/'session.log','w',encoding='utf-8')
        self.process=subprocess.Popen(args,cwd=ROOT,stdout=self.log_handle,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        self.play.configure(state='disabled',text='GAME RUNNING');self.status.set('Launching Reassembly…')

    def poll(self):
        if self.process is not None:
            code=self.process.poll()
            if code is None:
                log=ROOT/'session.log'
                started=log.exists() and '"type": "game-ready"' in log.read_text(encoding='utf-8',errors='replace')
                self.status.set('Reassembly is running with Build Menu Filters. Exit the game normally to save.' if started else 'Launching Reassembly; waiting for Steam and game initialization…')
            else:
                self.last_exit=code
                self.process=None
                if self.log_handle:self.log_handle.close();self.log_handle=None
                self.play.configure(text='PLAY REASSEMBLY');self.validate()
                self.settings=json.loads((ROOT/'settings.json').read_text())
                self.center.set(self.settings.get('toolbarX') is None)
                self.scale.set(self.settings.get('toolbarScale',1)*100);self.scale_label.configure(text=f'{self.scale.get():.0f}%')
                if code:self.status.set('The game could not finish starting. Open the session log for details.')
        self.root.after(500,self.poll)

    def view_log(self):
        path=ROOT/'session.log'
        if path.exists():os.startfile(path)
        else:messagebox.showinfo('Session log','Launch the game once to create a session log.')

def main():
    if '--backend' in sys.argv:
        sys.argv.remove('--backend')
        stream=open(ROOT/'session.log','a',encoding='utf-8',buffering=1)
        sys.stdout=sys.stderr=stream
        import launch
        try:launch.main()
        except Exception:
            import traceback
            traceback.print_exc();raise SystemExit(1)
        return
    parser=argparse.ArgumentParser();parser.add_argument('--preview',type=Path);parser.add_argument('--self-test',type=Path);parser.add_argument('--steam-test',action='store_true');args=parser.parse_args()
    if args.steam_test and not args.self_test:parser.error('--steam-test requires --self-test')
    if os.name=='nt':
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root=tk.Tk();root.tk.call('tk','scaling',96/72);app=Launcher(root)
    root.update_idletasks()
    root.geometry(f'+{max(0,(root.winfo_screenwidth()-960)//2)}+{max(0,(root.winfo_screenheight()-760)//2)}')
    if args.preview:
        def preview():
            from PIL import ImageGrab
            root.update_idletasks()
            ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(args.preview)
            assert app.validate();assert app.center.get()
            app.page('Interface');root.update_idletasks();app.page('Help');root.update_idletasks();root.destroy()
        root.after(800,preview)
    if args.self_test:
        original=(ROOT/'settings.json').read_text()
        app.test_session=True
        app.test_steam=args.steam_test
        def begin_test():
            saved_game=app.game.get();app.game.set(str(ROOT/'missing-game.exe'));assert not app.validate()
            app.game.set(saved_game);assert app.validate()
            app.page('Interface');app.scale.set(90);app.save()
            assert json.loads((ROOT/'settings.json').read_text())['toolbarScale']==.9
            app.reset_layout();assert json.loads((ROOT/'settings.json').read_text())['toolbarX'] is None
            app.page('Play');app.play.invoke();assert app.process is not None
            root.after(500,finish_test)
        def finish_test():
            if app.process is not None:root.after(500,finish_test);return
            import ctypes
            report={'guiPlayExit':app.last_exit,'consoleWindow':int(ctypes.windll.kernel32.GetConsoleWindow() or 0),
                    'settingsSaveAndReset':True,'badExecutableRejected':True,'steamStartupTest':app.test_steam}
            (ROOT/'settings.json').write_text(original)
            args.self_test.write_text(json.dumps(report,indent=2))
            root.destroy()
            if app.last_exit!=0:raise SystemExit(1)
            if getattr(sys,'frozen',False) and report['consoleWindow']!=0:raise SystemExit(1)
        root.after(300,begin_test)
    root.mainloop()

if __name__=='__main__':main()
