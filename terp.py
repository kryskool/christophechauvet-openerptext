#!/usr/bin/python
##############################################################################
#
#    TERP: a Text-mode ERP Client
#    Copyright (C) 2010 by Almacom (Thailand) Ltd.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from optparse import OptionParser
import curses
import curses.textpad
import sys
import time
import xmlrpclib
import xml.etree.ElementTree
import pdb
import traceback
import re

parser=OptionParser()
parser.add_option("-H","--host",dest="host",help="host name",metavar="HOST",default="127.0.0.1")
parser.add_option("-P","--port",dest="port",help="port number",metavar="PORT",type="int",default=8069)
parser.add_option("-d","--db",dest="dbname",help="database",metavar="DB")
parser.add_option("-u","--uid",dest="uid",help="user ID",metavar="UID",default=1)
parser.add_option("-p","--passwd",dest="passwd",help="user password",metavar="PASSWD",default="admin")
parser.add_option("--pref",action="store_true",dest="user_pref",help="edit user preferences",default=False)
parser.add_option("--debug",action="store_true",dest="debug",help="debug mode",default=False)
(opts,args)=parser.parse_args()

if opts.debug:
    def ex_info(type,value,tb):
        traceback.print_exception(type,value,tb)
        pdb.pm()
    sys.excepthook=ex_info

rpc=xmlrpclib.ServerProxy("http://%s:%d/xmlrpc/object"%(opts.host,opts.port))

dbname=opts.dbname
if not dbname:
    raise Exception("Missing dbname")
uid=opts.uid
passwd=opts.passwd

screen=None
root_panel=None
log_file=file("/tmp/terp.log","a")
dbg_mode=0

def log(*args):
    if not log_file:
        return
    msg=" ".join([str(a) for a in args])
    log_file.write(msg+"\n")
    log_file.flush()

def rpc_exec(*args):
    try:
        return rpc.execute(dbname,uid,passwd,*args)
    except Exception,e:
        raise Exception("rpc_exec failed: %s %s %s %s\n%s"%(dbname,uid,passwd,str(args),str(e)))

def rpc_exec_wkf(*args):
    try:
        return rpc.exec_workflow(dbname,uid,passwd,*args)
    except Exception,e:
        raise Exception("rpc_exec_wkf failed: %s %s %s %s\n%s"%(dbname,uid,passwd,str(args),str(e)))

def set_trace():
    curses.nocbreak()
    screen.keypad(0)
    curses.echo()
    curses.endwin()
    pdb.set_trace()

class Widget(object):
    def on_unfocus(self,arg,source):
        pass

    def __init__(self):
        self.x=None
        self.y=None
        self.w=None
        self.h=None
        self.maxw=None
        self.maxh=None
        self.borders=[0,0,0,0]
        self.padding=[0,0,0,0]
        self.valign="top"
        self.halign="left"
        self.cx=None
        self.cy=None
        self.states_f={}
        self.view_attrs={}
        self.states_v=None
        self.attrs_v={}
        self.colspan=1
        self.colspan_follow=0
        self.rowspan=1
        self.parent=None
        self.window=None
        self.win_x=None
        self.win_y=None
        self.listeners={
            "keypress": [],
            "unfocus": [],
        }
        self.add_event_listener("unfocus",self.on_unfocus)
        self.record=None
        self.name=None
        self.field=None
        self.invisible=False
        self.readonly=False
        self.update_readonly=True
        self.can_focus=False
        self.has_focus=False
        self.update_can_focus=False
        self.view_wg=None

    def to_s(self,d=0):
        s="  "*d
        s+=" "+self.__class__.__name__
        for name in dir(self):
            if name.startswith("_"):
                continue
            if not name in ("x","y","maxw","maxh","h","w","can_focus","has_focus","borders","padding","seps","string","cx","cy","colspan","col","readonly","required","invisible"):
                continue
            val=getattr(self,name)
            if callable(val):
                continue
            s+=" %s=%s"%(name,str(val))
        for name,val in self.view_attrs.items():
            s+=" %s=%s"%(name,str(val))
        return s

    def draw(self):
        raise Exception("method not implemented")

    def refresh(self):
        pass

    def get_tabindex(self):
        if self.can_focus:
            return [self]
        else:
            return []

    def add_event_listener(self,type,listener):
        self.listeners[type].append(listener)

    def process_event(self,event,param,source):
        processed=False
        for listener in self.listeners.get(event,[]):
            if listener(param,source):
                processed=True
        if processed:
            return True
        if self.parent:
            self.parent.process_event(event,param,source)

    def clear_focus(self):
        if self.has_focus:
            self.has_focus=False
            self.process_event("unfocus",None,self)

    def set_focus(self):
        self.has_focus=self.can_focus
        if self.has_focus:
            return self
        return None

    def get_focus(self):
        return self.has_focus and self or None

    def set_cursor(self):
        screen.move(self.win_y+self.y,self.win_x+self.x)

    def init_attrs(self):
        if self.field:
            if "string" in self.field:
                self.string=self.field["string"]
        if "string" in self.view_attrs:
            self.string=self.view_attrs["string"]
        if "colspan" in self.view_attrs:
            self.colspan=int(self.view_attrs["colspan"])
        if "col" in self.view_attrs:
            self.col=int(self.view_attrs["col"])

    def update_attrs(self):
        new_attrs={}
        if self.field:
            for attr in ("readonly","required","domain"):
                if attr in self.field:
                    new_attrs[attr]=self.field[attr]
            if "states" in self.field:
                state=self.eval_expr("state")
                vals=self.field["states"].get(state,[])
                for attr,val in vals:
                    new_attrs[attr]=val
        for attr in ('readonly','required','invisible'):
            if attr in self.view_attrs:
                res=self.eval_expr(self.view_attrs[attr])
                if res:
                    new_attrs[attr]=True
        if "domain" in self.view_attrs:
            new_attrs["domain"]=self.eval_expr(self.view_attrs["domain"]) or []
        if "context" in self.view_attrs:
            expr=self.view_attrs["context"]
            if expr[0]=="{":
                new_attrs["context"]=self.eval_expr(expr)
            else:
                ctx={}
                for expr_ in expr.split(","):
                    var,val=expr_.split("=")
                    ctx[var]=self.eval_expr(val) or {}
                new_attrs["context"]=ctx
        if "states" in self.view_attrs:
            states=self.view_attrs["states"].split(",")
            state=self.eval_expr("state")
            if not state in states:
                new_attrs["invisible"]=1
        if "attrs" in self.view_attrs:
            if self.record:
                attrs=self.eval_expr(self.view_attrs["attrs"])
                for attr,dom in attrs.items():
                    eval_dom=True
                    for (name,op,param) in dom:
                        val=self.record.get_val(name)
                        if op=="=":
                            res=val==param
                        elif op=="!=":
                            res=val!=param
                        elif op=="in":
                            res=val in param
                        elif op=="not in":
                            res=not val in param
                        if not res:
                            eval_dom=False
                            break
                    if eval_dom:
                        new_attrs[attr]=True
        for attr,val in new_attrs.items():
            if not attr in ("readonly","required","invisible","domain","context"):
                continue
            if attr=="readonly" and not self.update_readonly:
                continue
            setattr(self,attr,val)
        if self.update_can_focus:
            self.can_focus=not self.readonly

    def on_record_change(self):
        self.update_attrs()

    def on_field_change(self):
        pass

    def set_record(self,record):
        self.record=record
        record.add_event_listener("record_change",self.on_record_change)
        if self.name:
            record.add_event_listener("field_change_"+self.name,self.on_field_change)

    def eval_expr(self,expr):
        class Env(dict):
            def __init__(self,wg):
                self.__wg=wg
            def __getitem__(self,name):
                if name=="True":
                    return True
                elif name=="False":
                    return False
                elif name=="parent":
                    return Env(self.__wg.view_wg.parent)
                elif name=="context":
                    return self.__wg.view_wg.parent.context
                rec=self.__wg.record
                if not rec:
                    return False
                if not name in rec.fields:
                    return False
                val=rec.get_val(name)
                if rec.fields[name]['type']=='many2one' and val:
                    val=val[0]
                return val
            def __getattr__(self,name):
                if name=="__wg":
                    return self.__dict__["__wg"]
                return self[name]
        return eval(expr,Env(self))

class Panel(Widget):
    def __init__(self):
        super(Panel,self).__init__()
        self._childs=[]

    def add(self,wg):
        wg.parent=self
        self._childs.append(wg)

    def remove(self,wg):
        self._childs.remove(wg)

    def to_s(self,d=0):
        s=super(Panel,self).to_s(d)
        for c in self._childs:
            s+="\n"+c.to_s(d+1)
        return s

    def _vis_childs(self):
        for c in self._childs:
            if c.invisible:
                continue
            yield c

    def compute(self,h,w,y,x):
        self._compute_pass1()
        self.h=h
        self.w=w
        self.y=y
        self.x=x
        self._compute_pass2()

    def draw(self):
        for c in self._vis_childs():
            c.draw()

    def refresh(self):
        for c in self._vis_childs():
            c.refresh()

    def get_tabindex(self):
        ind=super(Panel,self).get_tabindex()
        for wg in self._vis_childs():
            ind+=wg.get_tabindex()
        return ind

    def clear_focus(self):
        super(Panel,self).clear_focus()
        for wg in self._childs:
            wg.clear_focus()

    def set_focus(self):
        res=super(Panel,self).set_focus()
        if res:
            return res
        for wg in self._childs:
            res=wg.set_focus()
            if res:
                return res

    def get_focus(self):
        wg_f=super(Panel,self).get_focus()
        if wg_f:
            return wg_f
        for wg in self._childs:
            wg_f=wg.get_focus()
            if wg_f:
                return wg_f
        return None

class ScrollPanel(Panel):
    def __init__(self):
        super(ScrollPanel,self).__init__()
        self.y0=0

    def _compute_pass1(self):
        if self._childs:
            wg=self._childs[0]
            wg._compute_pass1()
        else:
            wg=None
        if self.maxw is None:
            self.maxw=wg and wg.maxw or 1
            if self.maxw!=-1:
                self.maxw+=self.borders[1]+self.borders[3]+1
        if self.maxh is None:
            self.maxh=wg and wg.maxh or 1
            if self.maxh!=-1:
                self.maxh+=self.borders[0]+self.borders[2]

    def _compute_pass2(self):
        w=self.w-self.borders[1]-self.borders[3]-1
        h=self.h-self.borders[0]-self.borders[2]
        for wg in self._childs:
            wg.y=0
            wg.x=0
            wg.w=w
            wg.h=h
            wg.window=curses.newpad(wg.h+10,wg.w+10) #XXX
            wg.win_y=self.win_y+self.y+self.borders[0]
            wg.win_x=self.win_x+self.x+self.borders[3]
            wg._compute_pass2()

    def draw(self):
        win=self.window
        for wg in self._childs:
            wg.window.clear()
            wg.draw()
        if self.borders[0]:
            curses.textpad.rectangle(win,self.y,self.x,self.y+self.h-1,self.x+self.w-1)
        win.vline(self.y+self.borders[0],self.x+self.w-1-self.borders[1],curses.ACS_VLINE,self.h-self.borders[0]-self.borders[2])
        win.vline(self.y+self.borders[0],self.x+self.w-1-self.borders[1],curses.ACS_CKBOARD,3)

    def refresh(self):
        wg=self._childs[0]
        wg.window.refresh(self.y0,0,self.y+self.borders[0],self.x+self.borders[3],self.y+self.h-1-self.borders[2],self.x+self.w-1-self.borders[1]-1)
        wg.refresh()

class DeckPanel(Panel):
    def on_keypress(self,k,source):
        if k==curses.KEY_RIGHT:
            if source==self:
                chs=[wg for wg in self._vis_childs()]
                i=chs.index(self.cur_wg)
                i=(i+1)%len(chs)
                self.cur_wg=chs[i]
                root_panel.compute()
                root_panel.draw()
                root_panel.refresh()
                root_panel.set_cursor()
        elif k==curses.KEY_LEFT:
            if source==self:
                chs=[wg for wg in self._vis_childs()]
                i=chs.index(self.cur_wg)
                i=(i-1)%len(chs)
                self.cur_wg=chs[i]
                root_panel.compute()
                root_panel.draw()
                root_panel.refresh()
                root_panel.set_cursor()

    def __init__(self):
        super(DeckPanel,self).__init__()
        self.cur_wg=None
        self.add_event_listener("keypress",self.on_keypress)

    def add(self,wg):
        super(DeckPanel,self).add(wg)
        if self.cur_wg==None:
            self.cur_wg=wg

    def set_cur_wg(self,wg):
        self.cur_wg=wg

    def remove(self,wg):
        i=self._childs.index(wg)
        self._childs.pop(i)
        if wg==self.cur_wg:
            if self._childs:
                self.cur_wg=self._childs[i%len(self._childs)]
            else:
                self.cur_wg=None

    def _compute_pass1(self):
        if not self.cur_wg:
            return
        self.cur_wg._compute_pass1()
        if self.maxw is None:
            maxw=self.cur_wg.maxw
            if maxw==-1:
                self.maxw=-1
            else:
                self.maxw=maxw+self.borders[1]+self.borders[3]+self.padding[1]+self.padding[3]
        if self.maxh is None:
            maxh=self.cur_wg.maxh
            if maxh==-1:
                self.maxh=-1
            else:
                self.maxh=maxh+self.borders[0]+self.borders[2]+self.padding[0]+self.padding[2]

    def _compute_pass2(self):
        w=self.w-self.borders[1]-self.borders[3]-self.padding[1]-self.padding[3]
        h=self.h-self.borders[0]-self.borders[2]-self.padding[0]-self.padding[2]
        wg=self.cur_wg
        if wg.maxw==-1:
            wg.w=w
        else:
            wg.w=min(w,wg.maxw)
        if wg.maxh==-1:
            wg.h=h
        else:
            wg.h=min(h,wg.maxh)
        wg.y=self.y+self.borders[0]+self.padding[0]
        wg.x=self.x+self.borders[3]+self.padding[3]
        wg.window=self.window
        wg.win_y=self.win_y
        wg.win_x=self.win_x
        wg._compute_pass2()

    def draw(self):
        win=self.window
        if self.borders[0]:
            curses.textpad.rectangle(win,self.y,self.x,self.y+self.h-1,self.x+self.w-1)
        if self.cur_wg:
            self.cur_wg.draw()

    def refresh(self):
        if self.cur_wg:
            self.cur_wg.refresh()

    def set_focus(self):
        wg_f=Widget.set_focus(self)
        if wg_f:
            return wg_f
        if not self.cur_wg:
            return None
        return self.cur_wg.set_focus()

    def get_tabindex(self):
        ind=Widget.get_tabindex(self)
        if self.cur_wg:
            ind+=self.cur_wg.get_tabindex()
        return ind

class TabPanel(DeckPanel):
    def __init__(self):
        super(TabPanel,self).__init__()
        self.padding=[1,0,0,0]
        self.can_focus=True
        def on_keypress(k,source):
            if k==ord('c'):
                if source==self:
                    self.remove(self.cur_wg)
                    root_panel.draw()
                    root_panel.set_cursor()
        self.add_event_listener("keypress",on_keypress)

    def compute_tabs(self):
        x=self.x
        self.tab_x=[]
        for wg in self._childs:
            self.tab_x.append(x)
            x+=len(wg.name)+3

    def _compute_pass2(self):
        super(TabPanel,self)._compute_pass2()
        self.compute_tabs()

    def draw(self):
        win=self.window
        i=0
        for wg in self._childs:
            x=self.tab_x[i]
            s="%d %s "%(i+1,wg.name)
            if wg==self.cur_wg:
                win.addstr(self.y,x,s,curses.A_REVERSE)
            else:
                win.addstr(self.y,x,s)
            i+=1
        super(TabPanel,self).draw()

    def set_cursor(self):
        if not self.cur_wg:
            return
        i=self._childs.index(self.cur_wg)
        x=self.tab_x[i]
        screen.move(self.win_y+self.y,self.win_x+x)

class Notebook(DeckPanel):
    def __init__(self):
        super(Notebook,self).__init__()
        self.can_focus=True
        self.tab_x=[]
        self.borders=[1,1,1,1]

    def compute_tabs(self):
        x=self.x+3
        self.tab_x=[]
        for wg in self._childs:
            if wg.invisible:
                continue
            self.tab_x.append(x)
            x+=len(wg.string)+3

    def _compute_pass2(self):
        super(Notebook,self)._compute_pass2()
        self.compute_tabs()

    def draw(self):
        win=self.window
        super(Notebook,self).draw()
        i=0
        for wg in self._childs:
            if wg.invisible:
                continue
            x=self.tab_x[i]
            if x+len(wg.string)+1>=80:
                continue
            if i==0:
                win.addch(self.y,x-2,curses.ACS_RTEE)
            else:
                win.addch(self.y,x-2,curses.ACS_VLINE)
            s=" "+wg.string+" "
            if self.cur_wg==wg:
                win.addstr(self.y,x-1,s,curses.A_BOLD)
            else:
                win.addstr(self.y,x-1,s)
            if i==len(self._childs)-1:
                win.addch(self.y,x+len(wg.string)+1,curses.ACS_LTEE)
            i+=1

    def set_cursor(self):
        if not self.cur_wg:
            return
        chs=[wg for wg in self._vis_childs()]
        i=chs.index(self.cur_wg)
        x=self.tab_x[i]
        screen.move(self.win_y+self.y,self.win_x+x)

class Table(Panel):
    def __init__(self):
        super(Table,self).__init__()
        self.col=0
        self._childs=[]
        self.num_rows=0
        self.seps=[[(0,False)],[(0,False)]]
        self.h_top=None
        self.w_left=None
        self._next_cx=0
        self._next_cy=0

    def add(self,wg):
        if self._next_cx and self._next_cx+wg.colspan+wg.colspan_follow>self.col:
            self._next_cy+=1
            self._next_cx=0
        if wg.colspan>self.col:
            wg.colspan=self.col
        wg.cy=self._next_cy
        wg.cx=self._next_cx
        wg.parent=self
        self._childs.append(wg)
        self._next_cx+=wg.colspan
        self.num_rows=wg.cy+1

    def insert_row(self,cy,row):
        cx=0
        for wg in row:
            wg.cy=cy
            wg.cx=cx
            cx+=wg.colspan
            if cx>self.col:
                raise Exception("line too big")
        pos=None
        i=0
        for wg in self._childs:
            if wg.cy>=cy:
                if pos==None:
                    pos=i
                wg.cy+=1
            i+=1
        if pos==None:
            pos=len(self._childs)
        self._childs=self._childs[:pos]+row+self._childs[pos:]
        for wg in row:
            wg.parent=self
        self.num_rows+=1

    def delete_row(self,cy):
        self._childs=[wg for wg in self._childs if wg.cy!=cy]
        for wg in self._childs:
            if wg.cy>cy:
                wg.cy-=1
        self.num_rows-=1
        if self._next_cy>0:
            self._next_cy-=1

    def newline(self):
        self._next_cy+=1
        self._next_cx=0

    def _get_sep_size(self,type,i):
        if type=="y":
            seps=self.seps[0]
        elif type=="x":
            seps=self.seps[1]
        else:
            raise Exception("invalid separator type")
        if i==0:
            return 0
        elif i-1<len(seps):
            return seps[i-1][0]
        else:
            return seps[-1][0]

    def _get_sep_style(self,type,i):
        if type=="y":
            seps=self.seps[0]
        elif type=="x":
            seps=self.seps[1]
        else:
            raise Exception("invalid separator type")
        if i==0:
            return False
        elif i-1<len(seps):
            return seps[i-1][1]
        else:
            return seps[-1][1]

    def _total_sep_size(self,type):
        if type=="y":
            n=self.num_rows
        elif type=="x":
            n=self.col
        else:
            raise Exception("invalid separator type")
        return sum([self._get_sep_size(type,i) for i in range(n)])

    def _compute_pass1(self):
        for widget in self._vis_childs():
            if hasattr(widget,"_compute_pass1"):
                widget._compute_pass1()
        # 1. compute container max width
        if self.maxw is None:
            expand=False
            for wg in self._vis_childs():
                if wg.maxw==-1:
                    expand=True
                    break
            if expand:
                self.maxw=-1
            else:
                w_left=[0]
                for i in range(1,self.col+1):
                    w_max=w_left[i-1]
                    for wg in self._vis_childs():
                        cr=wg.cx+wg.colspan
                        if cr!=i:
                            continue
                        w=w_left[wg.cx]+self._get_sep_size("x",wg.cx)+wg.maxw
                        if w>w_max:
                            w_max=w
                    w_left.append(w_max)
                self.maxw=self.borders[3]+self.borders[1]+w_left[-1]
        # 2. compute container max height
        if self.maxh is None:
            expand=False
            for wg in self._vis_childs():
                if wg.maxh==-1:
                    expand=True
                    break
            if expand:
                self.maxh=-1
            else:
                h_top=[0]
                for i in range(1,self.num_rows+1):
                    h_max=h_top[i-1]
                    for wg in self._vis_childs():
                        cr=wg.cy+wg.rowspan
                        if cr!=i:
                            continue
                        h=h_top[wg.cy]+self._get_sep_size("y",wg.cy)+wg.maxh
                        if h>h_max:
                            h_max=h
                    h_top.append(h_max)
                self.maxh=self.borders[2]+self.borders[0]+h_top[-1]

    def _compute_pass2(self):
        if not self._childs:
            self.w=0
            return
        # 1. compute child widths
        w_avail=self.w-self.borders[3]-self.borders[1]
        for wg in self._vis_childs():
            wg.w=0
        w_left=[0]*(self.col+1)
        w_rest=w_avail
        # allocate space fairly to every child
        while w_rest>0:
            w_alloc=w_rest-self._total_sep_size("x")
            if w_alloc>self.col:
                dw=w_alloc/self.col
            else:
                dw=1
            incr=False
            for wg in self._vis_childs():
                if wg.maxw!=-1:
                    if not wg.w<wg.maxw:
                        continue
                    dw_=min(dw,wg.maxw-wg.w)
                else:
                    dw_=dw
                wg.w+=dw_
                incr=True
                w=w_left[wg.cx]+self._get_sep_size("x",wg.cx)+wg.w
                cr=wg.cx+wg.colspan
                if w>w_left[cr]:
                    dwl=w-w_left[cr]
                    for i in range(cr,self.col+1):
                        w_left[i]+=dwl
                    w_rest=w_avail-w_left[-1]
                    if w_rest==0:
                        break
            if not incr:
                break
        self.w_left=w_left
        # add extra cell space to regions
        for wg in self._vis_childs():
            if wg.maxw!=-1 and wg.w==wg.maxw:
                continue
            w=w_left[wg.cx]+self._get_sep_size("x",wg.cx)+wg.w
            cr=wg.cx+wg.colspan
            if w<w_left[cr]:
                dw=w_left[cr]-w
                if wg.maxw!=-1:
                    dw=min(dw,wg.maxw-wg.w)
                wg.w+=dw
        # 2. compute child heights
        h_avail=self.h-self.borders[2]-self.borders[0]
        for wg in self._vis_childs():
            wg.h=0
        h_top=[0]*(self.num_rows+1)
        h_rest=h_avail
        # allocate space fairly to every child
        while h_rest>0:
            h_alloc=h_rest-self._total_sep_size("y")
            if h_alloc>self.num_rows:
                dh=h_alloc/self.num_rows
            else:
                dh=1
            incr=False
            for wg in self._vis_childs():
                if wg.maxh!=-1:
                    if not wg.h<wg.maxh:
                        continue
                    dh_=min(dh,wg.maxh-wg.h)
                else:
                    dh_=dh
                wg.h+=dh_
                incr=True
                h=h_top[wg.cy]+self._get_sep_size("y",wg.cy)+wg.h
                cr=wg.cy+wg.rowspan
                if h>h_top[cr]:
                    dht=h-h_top[cr]
                    for i in range(cr,self.num_rows+1):
                        h_top[i]+=dht
                    h_rest=h_avail-h_top[-1]
                    if h_rest==0:
                        break
            if not incr:
                break
        self.h_top=h_top
        # add extra cell space to regions
        for wg in self._vis_childs():
            if wg.maxh!=-1 and wg.h==wg.maxh:
                continue
            h=h_top[wg.cy]+self._get_sep_size("y",wg.cy)+wg.h
            cr=wg.cy+wg.rowspan
            if h<h_top[cr]:
                dh=h_top[cr]-h
                if wg.maxh!=-1:
                    dh=min(dh,wg.maxh-wg.h)
                wg.h+=dh
        # 3. compute child positions
        for wg in self._vis_childs():
            if wg.valign=="top":
                wg.y=self.y+self.borders[0]+self.h_top[wg.cy]+self._get_sep_size("y",wg.cy)
            elif wg.valign=="bottom":
                wg.y=self.y+self.borders[0]+self.h_top[wg.cy+wg.rowspan]-wg.h
            else:
                raise Exception("invalid valign: %s"%wg.valign)
            if wg.halign=="left":
                wg.x=self.x+self.borders[3]+w_left[wg.cx]+self._get_sep_size("x",wg.cx)
            elif wg.halign=="right":
                wg.x=self.x+self.borders[3]+w_left[wg.cx+wg.colspan]-wg.w
            else:
                raise Exception("invalid halign: %s"%wg.valign)
            wg.window=self.window
            wg.win_y=self.win_y
            wg.win_x=self.win_x
        for child in self._vis_childs():
            if hasattr(child,"_compute_pass2"):
                child._compute_pass2()

    def draw(self):
        win=self.window
        # draw borders
        if self.borders[0]:
            curses.textpad.rectangle(win,self.y,self.x,self.y+self.h-1,self.x+self.w-1)
        # draw vertical separators
        x0=self.x+self.borders[3]
        y0=self.y+self.borders[0]-1
        y1=self.y+self.h-self.borders[2]
        for i in range(1,self.col):
            if self._get_sep_style("x",i):
                x=x0+self.w_left[i]
                win.vline(y0+1,x,curses.ACS_VLINE,y1-y0-1)
                win.addch(y0,x,curses.ACS_TTEE)
                win.addch(y1,x,curses.ACS_BTEE)
        # draw horizontal separators
        y0=self.y+self.borders[0]
        x0=self.x+self.borders[3]-1
        x1=self.x+self.w-self.borders[1]
        for i in range(1,self.num_rows):
            if self._get_sep_style("y",i):
                y=y0+self.h_top[i]
                win.hline(y,x0+1,curses.ACS_HLINE,x1-x0-1)
                win.addch(y,x0,curses.ACS_LTEE)
                win.addch(y,x1,curses.ACS_RTEE)
                for j in range(1,self.col):
                    if self._get_sep_style("x",j):
                        x=x0+self.w_left[j]
                        win.addch(y,x+1,curses.ACS_PLUS)
        # draw cell contents
        super(Table,self).draw()

class Form(Table):
    def __init__(self):
        super(Form,self).__init__()
        self.relation=None
        self.maxw=-1
        self.seps=[[(0,False)],[(1,False)]]
        self.col=4
        self.context={}

class Group(Table):
    def __init__(self):
        super(Group,self).__init__()
        self.col=4
        self.seps=[[(0,False)],[(1,False)]]

class Page(Table):
    def __init__(self):
        super(Page,self).__init__()
        self.col=4
        self.seps=[[(0,False)],[(1,False)]]

class HorizontalPanel(Table):
    def __init__(self):
        super(HorizontalPanel,self).__init__()
        self.seps=[[(0,False)],[(1,True)]]

    def add(self,wg):
        wg.colspan=1
        self.col+=1
        super(HorizontalPanel,self).add(wg)

class VerticalPanel(Table):
    def __init__(self):
        super(VerticalPanel,self).__init__()
        self.seps=[[(0,False)],[(0,True)]]
        self.col=1

    def add(self,wg):
        wg.colspan=1
        super(VerticalPanel,self).add(wg)

class ListLine(object):
    def __init__(self):
        self.depth=0
        self.open=False
        self.record=None
        self.childs=[]
        self.selected=False
        self.widgets=[]

class ListView(Table):
    def on_open(self,line_no):
        self.process_event("open",line_no,self)

    def on_keypress(self,k,source):
        if k==ord("\n"):
            if source in self._childs:
                i=self._childs.index(source)
                line_no=i/self.col
                if self.has_header:
                    line_no-=1
                self.on_open(line_no)
            return True
        elif k==ord(" "):
            if source in self._childs:
                i=self._childs.index(source)
                line_no=i/self.col
                if self.has_header:
                    line_no-=1
                line=self.lines[line_no]
                line.selected=not line.selected
                root_panel.draw()
                root_panel.refresh()
                root_panel.set_cursor()
            return True

    def __init__(self):
        super(ListView,self).__init__()
        self.relation=None
        self.seps=[[(0,False)],[(1,True)]]
        self.lines=[]
        self.num_lines=0
        self.has_header=False
        self.listeners.update({
            "open": [],
        })
        self.add_event_listener("keypress",self.on_keypress)

    def make_header(self,headers):
        for header in headers:
            wg=Label()
            wg.string=header
            self.add(wg)
        self.has_header=True

    def make_line_widgets(self,line):
        widgets=[]
        for i in range(self.col):
            wg=Label()
            wg.string=line.record.vals["name"]
            if i==0:
                wg.can_focus=True
            widgets.append(wg)
        return widgets

    def add_line(self,line):
        self.lines.append(line)
        self.num_lines+=1
        widgets=self.make_line_widgets(line)
        line.widgets=widgets
        for wg in widgets:
            self.add(wg)

    def add_lines(self,lines):
        for line in lines:
            self.add_line(line)

    def add_records(self,recs):
        lines=[]
        for rec in recs:
            line=ListLine()
            line.record=rec
            lines.append(line)
        self.add_lines(lines)

    def insert_line(self,line_no,line):
        self.lines.insert(line_no,line)
        self.num_lines+=1
        widgets=self.make_line_widgets(line)
        line.widgets=widgets
        row_no=line_no+(self.has_header and 1 or 0)
        self.insert_row(row_no,widgets)

    def insert_lines(self,line_no,lines):
        i=line_no
        for line in lines:
            self.insert_line(i,line)
            i+=1

    def insert_records(self,recs):
        lines=[]
        for rec in recs:
            line=ListLine()
            line.record=rec
            lines.append(line)
        self.insert_lines(lines)

    def delete_line(self,line_no):
        self.lines.pop(line_no)
        self.num_lines-=1
        row_no=line_no+(self.has_header and 1 or 0)
        self.delete_row(row_no)

    def delete_lines(self,line_no=None,num=None):
        if line_no==None:
            line_no=0
            num=self.num_lines
        elif num==None:
            num=1
        for i in range(num):
            self.delete_line(line_no)

    def set_cursor(self):
        screen.move(self.win_y+self.y+self.borders[0]+(self.has_header and 1+self.seps[0][0][0] or 0),self.win_x+self.x+self.borders[3])

    def draw(self):
        win=self.window
        super(ListView,self).draw()
        for line in self.lines:
            if line.selected:
                wg=line.widgets[0]
                y=wg.y
                for i in range(self.col):
                    x0=self.x+self.borders[3]+self.w_left[i]+self._get_sep_size("x",i)
                    x1=self.x+self.borders[3]+self.w_left[i+1]
                    win.chgat(y,x0,x1-x0,curses.A_REVERSE)

    def set_lines(self,lines):
        self.delete_lines()
        self.add_lines(lines)

class TreeView(ListView):
    def on_keypress(self,k,source):
        if k==curses.KEY_RIGHT:
            if source in self._childs:
                i=self._childs.index(source)
                row_no=i/self.col
                line_no=row_no-(self.has_header and 1 or 0)
                line=self.lines[line_no]
                if not line.open:
                    self.process_event("expand",line_no,self)
                    self.insert_lines(line_no+1,line.childs)
                    line.open=True
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.set_cursor()
            return True
        elif k==curses.KEY_LEFT:
            if source in self._childs:
                i=self._childs.index(source)
                row_no=i/self.col
                line_no=row_no-(self.has_header and 1 or 0)
                line=self.lines[line_no]
                if line.open:
                    i=line_no+1
                    d=line.depth
                    while i<len(self.lines) and self.lines[i].depth>d:
                        i+=1
                    self.delete_lines(line_no+1,i-(line_no+1))
                    line.open=False
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.set_cursor()
            return True
        return super(TreeView,self).on_keypress(k,source)

    def __init__(self):
        super(TreeView,self).__init__()
        self.items={}
        self.listeners.update({
            "expand": [],
        })

class Label(Widget):
    def __init__(self):
        super(Label,self).__init__()
        self.maxh=1
        self.string=""

    def _compute_pass1(self):
        self.maxw=len(self.string)

    def draw(self):
        win=self.window
        s=self.string[:self.w]
        win.addstr(self.y,self.x,s)

class Separator(Widget):
    def __init__(self):
        super(Separator,self).__init__()
        self.maxh=1
        self.maxw=-1

    def draw(self):
        win=self.window
        s="_"
        if self.string:
            s+=self.string[:self.w-1]
        s+="_"*(self.w-len(s))
        win.addstr(self.y,self.x,s)

class Button(Widget):
    def on_keypress(self,k,source):
        if source==self and k==ord("\n"):
            self.process_event("push",None,self)

    def on_push(self,arg,source):
        pass

    def __init__(self):
        super(Button,self).__init__()
        self.can_focus=True
        self.maxh=1
        self.listeners["push"]=[]
        self.add_event_listener("keypress",self.on_keypress)
        self.add_event_listener("push",self.on_push)

    def _compute_pass1(self):
        self.maxw=len(self.string)+2

    def draw(self):
        win=self.window
        s="["+self.string[:self.w-2]+"]"
        win.addstr(self.y,self.x,s)

    def set_cursor(self):
        screen.move(self.win_y+self.y,self.win_x+self.x+1)

class FormButton(Button):
    def on_push(self,arg,source):
        type=getattr(self,"type","wizard")
        if type=="wizard":
            rpc_exec_wkf(form.model,self.name,self.view_wg.obj_id)
            self.view_wg.read()
            root_panel.clear_focus()
            root_panel.set_focus()
            root_panel.set_cursor()
        else:
            raise Exception("invalid button type: %s"%type)

class FieldLabel(Widget):
    def __init__(self):
        super(FieldLabel,self).__init__()
        self.halign="right"
        self.maxh=1

    def _compute_pass1(self):
        self.maxw=len(self.string)+1

    def draw(self):
        win=self.window
        s=self.string[:self.w-1]
        s+=":"
        win.addstr(self.y,self.x,s)

class Input(Widget):
    def __init__(self):
        super(Input,self).__init__()
        self.name=None
        self.under=True
        self.domain=None
        self.context=None
        self.field=None
        self.record=None
        self.can_focus=True
        self.update_can_focus=True

    def get_val(self):
        return self.record.get_val(self.name)

    def set_val(self,val):
        self.record.set_val(self.name,val)

    def apply_on_change(self):
        expr=self.view_attrs.get('on_change')
        if not expr:
            return
        log('=====================')
        log('apply_on_change',self.name,self.record.model,self.record.id,expr)
        i=expr.find("(")
        if i==-1:
            raise Exception("invalid on_change expression: %s"%expr)
        func=expr[:i].strip()
        args_str=expr[i:]
        args=self.eval_expr(args_str)
        if type(args)!=type(()):
            args=(args,)
        ids=[self.record.id or False]
        log('  ',func,args)
        res=rpc_exec(self.record.model,func,ids,*args)
        if res and "value" in res:
            vals=res["value"]
            log('vals',vals)
            for name,val in vals.items():
                field=self.record.fields[name]
                if field['type']=='many2many':
                    vals[name]=[ObjRecord(field['relation'],id) for id in val or []]
                elif field['type']=='many2one':
                    vals[name]=ObjRecord.convert_m2o(val,field['relation'])
            self.record.set_vals(vals,self.record.fields)
            root_panel.draw()
            root_panel.refresh()

    def on_field_change(self):
        log('on_field_change',self.name,self.get_val())
        pass

class StringInput(Input):
    def on_keypress(self,k,source):
        if self.readonly:
            return
        if curses.ascii.isprint(k):
            new_str=self.str_val[:self.cur_pos]+chr(k)+self.str_val[self.cur_pos:]
            if self.is_valid(new_str):
                self.str_val=new_str
                self.cur_pos+=1
                if self.cur_pos-self.cur_origin>self.w-1:
                    self.cur_origin=self.cur_pos-self.w+1
                self.process_event("edit",new_str,self)
        elif k==curses.KEY_LEFT:
            self.cur_pos=max(self.cur_pos-1,0)
            if self.cur_pos<self.cur_origin:
                self.cur_origin=self.cur_pos
                self.draw()
            self.set_cursor()
        elif k==curses.KEY_RIGHT:
            self.cur_pos=min(self.cur_pos+1,len(self.str_val))
            if self.cur_pos-self.cur_origin>self.w-1:
                self.cur_origin=self.cur_pos-self.w+1
                self.draw()
            self.set_cursor()
        elif k==263:
            if self.cur_pos>=1:
                new_str=self.str_val[:self.cur_pos-1]+self.str_val[self.cur_pos:]
                if not new_str or self.is_valid(new_str):
                    self.str_val=new_str
                    self.cur_pos-=1
                    if self.cur_pos<self.cur_origin:
                        self.cur_origin=self.cur_pos
                    self.process_event("edit",new_str,self)
        elif k==330:
            if self.cur_pos<=len(self.str_val)-1:
                new_str=self.str_val[:self.cur_pos]+self.str_val[self.cur_pos+1:]
                if not new_str or self.is_valid(new_str):
                    self.str_val=new_str
                    self.process_event("edit",new_str,self)

    def on_edit(self,string,source):
        self.draw()
        self.to_screen()
        self.set_cursor()

    def on_field_change(self):
        super(StringInput,self).on_field_change()
        val=self.get_val()
        self.str_val=self.val_to_str(val)
        self.cur_pos=0
        self.cur_origin=0

    def __init__(self):
        super(StringInput,self).__init__()
        self.add_event_listener("keypress",self.on_keypress)
        self.listeners["edit"]=[]
        self.add_event_listener("edit",self.on_edit)
        self.cur_pos=0
        self.cur_origin=0
        self.str_val=""
        self.maxh=1

    def is_valid(self,string):
        return True

    def set_cursor(self):
        screen.move(self.win_y+self.y,self.win_x+self.x+self.cur_pos-self.cur_origin)

    def draw(self):
        win=self.window
        s=self.str_val[self.cur_origin:self.cur_origin+self.w]
        s=s.encode('ascii','replace')
        s+="_"*(self.w-len(s))
        win.addstr(self.y,self.x,s)

    def to_screen(self):
        win=self.window
        win.refresh(self.y,self.x,self.win_y+self.y,self.win_x+self.x,self.win_y+self.y,self.win_x+self.x+self.w)

    def _compute_pass1(self):
        if self.readonly:
            self.maxw=len(self.str_val)
        else:
            self.maxw=-1

    def on_unfocus(self,arg,source):
        if not self.readonly:
            val=self.str_to_val(self.str_val)
            old_val=self.get_val()
            if val!=old_val:
                self.set_val(val)
                self.apply_on_change()

class InputChar(StringInput):
    def val_to_str(self,val):
        return val and str(val) or ""

    def str_to_val(self,s):
        if s=="":
            return False
        return s

class LineOpener(StringInput):
    def __init__(self):
        super(LineOpener,self).__init__()
        del self.listeners['keypress']

    def val_to_str(self,val):
        if not val:
            return ""
        str=""
        str+="  "*self.line.depth
        if self.record.vals[self.field_parent]:
            str+="/"
        str+=val
        return str

    def str_to_val(self,s):
        if s=="":
            return False
        return s

class InputInteger(StringInput):
    def val_to_str(self,val):
        if val is False:
            return ""
        return str(val)

    def is_valid(self,string):
        try:
            x=int(string)
            return True
        except:
            return False

    def str_to_val(self,s):
        if s=="":
            return False
        return int(s)

class InputFloat(StringInput):
    def val_to_str(self,val):
        if val is False:
            return ""
        return "%.2f"%val

    def is_valid(self,string):
        try:
            x=float(string)
            return True
        except:
            return False

    def str_to_val(self,s):
        if s=="":
            return False
        return float(s)

class InputSelect(StringInput):
    def on_keypress(self,k,source):
        super(InputSelect,self).on_keypress(k,source)
        if k==ord("\n"):
            wg=SelectBox()
            wg.selection=self.field["selection"]
            def on_close(val):
                self.set_val(val)
                root_panel.draw()
                root_panel.refresh()
                self.set_focus()
                self.set_cursor()
            wg.on_close=on_close
            wg.show(self.win_y+self.y,self.win_x+self.x,self.str_val)

    def __init__(self):
        super(InputSelect,self).__init__()

    def val_to_str(self,val):
        if val is False:
            return ""
        for k,v in self.field["selection"]:
            if k==val:
                return v
        return ""

    def on_edit(self,string,source):
        if self.get_val():
            self.set_val(False)
        super(InputSelect,self).on_edit(string,source)

    def on_unfocus(self,arg,source):
        pass

class InputBoolean(StringInput):
    def val_to_str(self,val):
        return val and "Y" or "N"

    def is_valid(self,string):
        return string in ("Y","N")

    def str_to_val(self,s):
        if s in ("","N"):
            return False
        return True

class InputDate(StringInput):
    def on_keypress(self,k,source):
        super(InputDate,self).on_keypress(k,source)
        if k==ord("\n"):
            if not self.str_val:
                self.set_val(time.strftime("%Y-%m-%d"))
                self.draw()
                self.to_screen()
                self.set_cursor()

    def val_to_str(self,val):
        if val is False:
            return ""
        return val

    def str_to_val(self,s):
        if s=="":
            return False
        return s

class InputDatetime(StringInput):
    def val_to_str(self,val):
        if val is False:
            return ""
        return val

    def str_to_val(self,s):
        if s=="":
            return False
        return s

class InputM2O(StringInput):
    def on_keypress(self,k,source):
        super(InputM2O,self).on_keypress(k,source)
        if k==ord("\n"):
            wg=SearchPopup()
            wg.string=self.field["string"]
            wg.model=self.field["relation"]
            wg.query=self.str_val
            def on_close(ids):
                if ids:
                    id=ids[0]
                    self.set_val(id)
                    self.apply_on_change()
                root_panel.close_popup(wg)
                root_panel.clear_focus()
                self.set_focus()
                self.set_cursor()
            wg.on_close=on_close
            wg.show()

    def __init__(self):
        super(InputM2O,self).__init__()
        self.can_focus=True
        self.update_can_focus=False

    def on_edit(self,string,source):
        if self.get_val():
            self.set_val(False)
        super(InputM2O,self).on_edit(string,source)

    def val_to_str(self,val):
        if val is False:
            return ""
        return val[1]

    def on_unfocus(self,arg,source):
        pass

class InputReference(StringInput):
    def val_to_str(self,val):
        if val is False:
            return ""
        return str(val)

    def str_to_val(self,s):
        return False

class InputText(Input):
    def on_keypress(self,k,source):
        if self.readonly:
            return False
        if curses.ascii.isprint(k):
            line=self.lines[self.cur_y]
            new_line=line[:self.cur_x]+chr(k)+line[self.cur_x:]
            self.lines[self.cur_y]=new_line
            self.cur_x+=1
            if self.cur_x-self.cur_x0>(self.w-2)-1:
                self.cur_x0=self.cur_x-(self.w-2)+1
            self.draw()
            self.to_screen()
            self.set_cursor()
            return True
        elif k==curses.KEY_LEFT:
            self.cur_x=max(self.cur_x-1,0)
            if self.cur_x<self.cur_x0:
                self.cur_x0=self.cur_x
                self.draw()
                self.to_screen()
            self.set_cursor()
            return True
        elif k==curses.KEY_RIGHT:
            line=self.lines[self.cur_y]
            self.cur_x=min(self.cur_x+1,len(line))
            if self.cur_x-self.cur_x0>(self.w-2)-1:
                self.cur_x0=self.cur_x-(self.w-2)+1
                self.draw()
                self.to_screen()
            self.set_cursor()
            return True
        elif k==curses.KEY_UP:
            if self.cur_y>0:
                self.cur_y-=1
                self.cur_x=min(self.cur_x,len(self.lines[self.cur_y]))
                if self.cur_y<self.cur_y0:
                    self.cur_y0=self.cur_y
                    self.draw()
                    self.to_screen()
                self.set_cursor()
                return True
        elif k==curses.KEY_DOWN:
            if self.cur_y<len(self.lines)-1:
                self.cur_y+=1
                self.cur_x=min(self.cur_x,len(self.lines[self.cur_y]))
                if self.cur_y-self.cur_y0>(self.h-2)-1:
                    self.cur_y0=self.cur_y-(self.h-2)+1
                    self.draw()
                    self.to_screen()
                self.set_cursor()
                return True
        elif k==263:
            if self.cur_x>=1:
                line=self.lines[self.cur_y]
                new_line=line[:self.cur_x-1]+line[self.cur_x:]
                self.lines[self.cur_y]=new_line
                self.cur_x-=1
                if self.cur_x<self.cur_x0:
                    self.cur_x0=self.cur_x
                self.draw()
                self.to_screen()
                self.set_cursor()
            elif self.cur_y>0:
                line=self.lines.pop(self.cur_y)
                prev_line=self.lines[self.cur_y-1]
                self.lines[self.cur_y-1]=prev_line+line
                self.cur_y-=1
                if self.cur_y<self.cur_y0:
                    self.cur_y0=self.cur_y
                self.cur_x=len(prev_line)
                self.draw()
                self.to_screen()
                self.set_cursor()
            return True
        elif k==330:
            line=self.lines[self.cur_y]
            if self.cur_x<=len(line)-1:
                new_line=line[:self.cur_x]+line[self.cur_x+1:]
                self.lines[self.cur_y]=new_line
                self.draw()
                self.to_screen()
                self.set_cursor()
            elif self.cur_y<len(self.lines)-1:
                next_line=self.lines.pop(self.cur_y+1)
                line=self.lines[self.cur_y]
                self.lines[self.cur_y]=line+next_line
                self.draw()
                self.to_screen()
                self.set_cursor()
            return True
        elif k==ord('\n'):
            line=self.lines[self.cur_y]
            new_line=line[self.cur_x:]
            self.lines[self.cur_y]=line[:self.cur_x]
            self.lines.insert(self.cur_y+1,new_line)
            self.cur_y+=1
            if self.cur_y-self.cur_y0>(self.h-2)-1:
                self.cur_y0=self.cur_y-(self.h-2)+1
            self.cur_x=0
            self.cur_x0=0
            self.draw()
            self.to_screen()
            self.set_cursor()
            return True

    def on_field_change(self):
        super(InputText,self).on_field_change()
        val=self.get_val()
        self.lines=val and val.split("\n") or [""]
        self.cur_x=0
        self.cur_x0=0
        self.cur_y=0
        self.cur_y0=0

    def __init__(self):
        super(InputText,self).__init__()
        self.maxh=7
        self.maxw=-1
        self.cur_y=0
        self.cur_y0=0
        self.cur_x=0
        self.cur_x0=0
        self.lines=[]
        self.add_event_listener("keypress",self.on_keypress)

    def draw(self):
        win=self.window
        curses.textpad.rectangle(win,self.y,self.x,self.y+self.h-1,self.x+self.w-1)
        for i in range(self.h-2):
            line_no=i+self.cur_y0
            if line_no<len(self.lines):
                line=self.lines[line_no]
            else:
                line=""
            s=line[self.cur_x0:self.cur_x0+self.w-2]
            s=s.encode('ascii','replace')
            s+=" "*(self.w-2-len(s))
            win.addstr(self.y+1+i,self.x+1,s)

    def set_cursor(self):
        screen.move(self.win_y+self.y+1+self.cur_y-self.cur_y0,self.win_x+self.x+1+self.cur_x-self.cur_x0)

    def to_screen(self):
        win=self.window
        win.refresh(self.y,self.x,self.win_y+self.y,self.win_x+self.x,self.win_y+self.y+self.h,self.win_x+self.x+self.w)

    def _compute_pass1(self):
        if self.readonly:
            self.maxw=max([len(line) for line in self.lines])+2
        else:
            self.maxw=-1

    def on_unfocus(self,arg,source):
        if not self.readonly:
            val='\n'.join(self.lines) or False
            old_val=self.get_val()
            if val!=old_val:
                self.set_val(val)
                self.apply_on_change()

class ObjRecord(object):
    def __init__(self,model,id=None):
        self.model=model
        self.id=id
        self.vals={}
        self.fields={}
        self.changed=False
        self.deleted=False
        self.listeners={}

    def add_event_listener(self,event,listener):
        self.listeners.setdefault(event,[]).append(listener)

    def remove_event_listener(self,event,listener=None):
        if listener:
            self.listeners[event].remove(listener)
        else:
            self.listeners[event]=[]

    def process_event(self,event):
        log("record event",self.model,self.id,event,":",len(self.listeners.get(event,[])),"listeners")
        for listener in self.listeners.get(event,[]):
            listener()
        return True

    def get_val(self,name,default=None):
        return self.vals.get(name,default)

    def set_val(self,name,val):
        field=self.fields[name]
        if field['type']=='many2one':
            val=ObjRecord.convert_m2o(val,field['relation'])
        self.vals[name]=val
        self.changed=True
        self.record_changed([name])

    def set_vals(self,vals,fields):
        for name,val in vals.items():
            field=fields[name]
            if field['type']=='many2one':
                val=ObjRecord.convert_m2o(val,field['relation'])
            self.vals[name]=val
            self.fields[name]=field
        self.changed=True
        self.record_changed(vals.keys())

    @staticmethod
    def convert_m2o(val,model):
        if type(val)==type(1):
            name=rpc_exec(model,'name_get',[val])[0][1]
            val=(val,name)
        return val

    def read(self,fields,context=None):
        names=[name for name in fields.keys() if name not in self.vals]
        if not names:
            return
        if self.id:
            res=rpc_exec(self.model,"read",[self.id],names,context or {})[0]
        else:
            res=rpc_exec(self.model,"default_get",names,context or {})
        for name in names:
            field=fields[name]
            self.fields[name]=field
            val=res.get(name,False)
            if field['type']=='many2one':
                val=ObjRecord.convert_m2o(val,field['relation'])
            elif field['type'] in ('one2many','many2many'):
                ids=val or []
                val=[ObjRecord(field['relation'],id) for id in ids]
            self.vals[name]=val
        self.record_changed(fields.keys())

    @staticmethod
    def read_list(model,recs,fields,context=None):
        ids={}
        for rec in recs:
            if rec.id:
                ids[rec.id]=rec
        res=rpc_exec(model,"read",ids.keys(),fields,context or {})
        for r in res:
            rec=ids[r["id"]]
            for name,val in r.items():
                if name=="id":
                    continue
                if name not in rec.vals:
                    rec.vals[name]=val
                    rec.fields[name]=fields[name]
        return recs

    def clear(self):
        self.vals={}
        self.fields={}

    @staticmethod
    def clear_list(recs):
        for rec in recs:
            rec.clear()

    def get_op(self):
        if self.deleted:
            if not self.id:
                return None
            return (2,self.id)
        if not self.changed:
            return None
        vals_={}
        for name,val in self.vals.items():
            field=self.fields[name]
            if field["type"]=="many2one":
                val_=val and val[0] or False
            elif field["type"]=="many2many":
                ids=[rec.id for rec in val if not rec.deleted]
                val_=[(6,0,ids)]
            elif field["type"]=="one2many":
                val_=[]
                for rec in val:
                    op=rec.get_op()
                    if op:
                        val_.append(op)
            else:
                val_=val
            vals_[name]=val_
        if self.id:
            return (1,self.id,vals_)
        else:
            return (0,0,vals_)

    @staticmethod
    def save(recs):
        for rec in recs:
            op=rec.get_op()
            log("============")
            log("SAVE",rec.model,op)
            if not op:
                continue
            if op[0]==0:
                rec.id=rpc_exec(rec.model,"create",op[2])
            elif op[0]==1:
                rpc_exec(rec.model,"write",[op[1]],op[2])
            elif op[0]==2:
                rpc_exec(rec.model,"unlink",[op[1]])
        ObjRecord.after_save(recs)

    @staticmethod
    def after_save(recs):
        recs[:]=[rec for rec in recs if not rec.deleted]
        for rec in recs:
            rec.changed=False
            for name,val in rec.vals.items():
                field=rec.fields[name]
                if field["type"] in ("one2many","many2many"):
                    ObjRecord.after_save(val)

    def copy(self):
        rec=ObjRecord(self.model,self.id)
        rec.vals=self.vals.copy()
        rec.fields=self.fields.copy()
        rec.changed=True
        return rec

    def record_changed(self,names=None):
        self.process_event("record_change")
        if not names:
            names=self.fields.keys()
        for name in names:
            self.process_event("field_change_"+name)

class ObjBrowser(DeckPanel):
    def __init__(self,model,name=None,type=None,modes=None,view_ids=None,views=None,context=None,window=False,add=False):
        super(ObjBrowser,self).__init__()
        self.model=model
        self.type=type or "form"
        self.modes=modes or ["tree","form"]
        self.name=name or ""
        self.context=context or {}
        self.cur_mode=self.modes[0]
        self.mode_wg={}
        self.records=[]
        for mode in self.modes:
            if mode=="tree":
                wg=TreeMode(type=self.type)
            elif mode=="form":
                wg=FormMode()
            else:
                continue
            self.mode_wg[mode]=wg
            self.add(wg)
            wg.set_commands(self.type,self.modes,window=window,add=add)
            wg.maxh=-1
            if views and mode in views:
                wg.view=views[mode]
            if view_ids and mode in view_ids:
                wg.view_id=view_ids[mode]

    def load_view(self):
        self.mode_wg[self.cur_mode].load_view()

    def read(self):
        self.mode_wg[self.cur_mode].read()

class TreeMode(HorizontalPanel):
    def on_keypress(self,k,source):
        if k==curses.KEY_RIGHT:
            if source==self:
                i=self.commands.index(self.cur_cmd)
                i=(i+1)%len(self.commands)
                self.cur_cmd=self.commands[i]
                root_panel.set_cursor()
        elif k==curses.KEY_LEFT:
            if source==self:
                i=self.commands.index(self.cur_cmd)
                i=(i-1)%len(self.commands)
                self.cur_cmd=self.commands[i]
                root_panel.set_cursor()
        elif k==ord('\n'):
            if source==self:
                if self.cur_cmd=="N":
                    if self.parent.view_wg:
                        link=LinkPopup()
                        link.record=self.parent.record
                        link.view_wg=self.parent.view_wg
                        link.string=self.parent.field["string"]
                        link.form_mode.view=self.parent.field["views"].get("form")
                        link.form_mode.record=ObjRecord(self.parent.model)
                        link.form_mode.load_view()
                        link.form_mode.record.read(link.form_mode.view["fields"])
                        def on_close(save=False):
                            if save:
                                rec=link.form_mode.record.copy()
                                self.parent.records.append(rec)
                                self.read()
                            root_panel.close_popup(link)
                            root_panel.clear_focus()
                            self.set_focus()
                            self.set_cursor()
                        link.on_close=on_close
                        link.show()
                    else:
                        rec=ObjRecord(self.parent.model)
                        self.parent.cur_mode="form"
                        self.parent.mode_wg['form'].record=rec
                        self.parent.load_view()
                        self.parent.read()
                        self.parent.cur_wg=self.parent.mode_wg["form"]
                        root_panel.compute()
                        root_panel.draw()
                        root_panel.refresh()
                        root_panel.clear_focus()
                        root_panel.set_focus()
                        root_panel.set_cursor()
                elif self.cur_cmd=="+":
                    wg=SearchPopup()
                    wg.string=self.parent.field["string"]
                    wg.model=self.parent.model
                    def on_close(ids):
                        if ids:
                            recs=self.parent.get_val()
                            recs+=[ObjRecord(self.parent.model,id) for id in ids]
                            self.parent.set_val(recs)
                        root_panel.close_popup(wg)
                        root_panel.clear_focus()
                        self.set_focus()
                        self.set_cursor()
                    wg.on_close=on_close
                    wg.show()
                elif self.cur_cmd=="S":
                    ObjRecord.save(self.parent.records)
                    ObjRecord.clear_list(self.parent.records)
                    self.read()
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    self.set_cursor()
                elif self.cur_cmd=="D":
                    mb=MessageBox()
                    mb.set_title("Confirmation")
                    mb.set_message("Are you sure to remove these records?")
                    mb.set_buttons(["Cancel","OK"])
                    def on_close(string):
                        if string=="OK":
                            for line in self.tree.lines:
                                if line.selected:
                                    line.record.deleted=True
                            if not self.parent.view_wg:
                                ObjRecord.save(self.parent.records)
                                ObjRecord.clear_list(self.parent.records)
                            self.read()
                    mb.on_close=on_close
                    mb.show()
                elif self.cur_cmd=="-":
                    for line in self.tree.lines:
                        if line.selected:
                            line.record.deleted=True
                    self.read()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.set_cursor()
                elif self.cur_cmd=="<":
                    pass
                elif self.cur_cmd==">":
                    pass
                elif self.cur_cmd=="T":
                    pass
                elif self.cur_cmd=="F":
                    sel_lines=[line for line in self.tree.lines if line.selected]
                    if sel_lines:
                        line=sel_lines[0]
                        rec=line.record
                        self.parent.cur_mode="form"
                        self.parent.mode_wg['form'].record=rec
                        self.parent.load_view()
                        self.parent.read()
                        self.parent.cur_wg=self.parent.mode_wg["form"]
                        root_panel.compute()
                        root_panel.draw()
                        root_panel.refresh()
                        root_panel.clear_focus()
                        root_panel.set_focus()
                        root_panel.set_cursor()

    def __init__(self,type):
        super(TreeMode,self).__init__()
        self.type=type
        self.borders=[1,1,1,1]
        self.add_event_listener("keypress",self.on_keypress)
        self.tree=None
        self.view=None
        self.view_id=None
        self.commands=None
        self.can_focus=False
        self.rec_child_pool={}
        if type=="tree":
            self.root_list=ListView()
            self.root_list.col=1
            self.root_list.names=["name"]
            self.root_list.maxh=-1
            self.root_list.borders=[0,0,0,0]
            self.add(self.root_list)
            def on_open(line_no,source):
                for line in self.root_list.lines:
                    line.selected=False
                line=self.root_list.lines[line_no]
                line.selected=True
                root_rec=line.record
                child_ids=root_rec.get_val(self.view["field_parent"])
                child_recs=self.read_child_records(child_ids)
                self.tree.delete_lines()
                self.tree.add_records(child_recs)
                root_panel.compute()
                root_panel.draw()
                root_panel.refresh()
                root_panel.clear_focus()
                self.tree.set_focus()
                root_panel.set_cursor()
            self.root_list.add_event_listener("open",on_open)
        elif type=="form":
            pass

    def set_commands(self,type,modes,window=False,add=False):
        self.commands=[]
        if type=="form":
            self.commands+=[add and "+" or "N",add and "-" or "D"]
            if window:
                self.commands+=["S","R"]
            self.commands+=["<",">"]
        self.commands+=[mode[0].upper() for mode in modes]
        self.cur_cmd="T"
        self.can_focus=True

    def draw(self):
        super(TreeMode,self).draw()
        if self.commands:
            win=self.window
            s=" ".join(self.commands)
            x=self.x+self.w-len(s)-3
            win.addch(self.y,x,curses.ACS_RTEE)
            x+=1
            win.addstr(self.y,x,s)
            x+=len(s)
            win.addch(self.y,x,curses.ACS_LTEE)

    def set_cursor(self):
        i=self.commands.index(self.cur_cmd)
        x=self.x+self.w-len(self.commands)*2-1+i*2
        screen.move(self.win_y+self.y,self.win_x+x)

    def parse(self,el,fields):
        if el.tag=="tree":
            wg=TreeView()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            headers=[]
            for child in el:
                name=child.attrib["name"]
                if child.tag=="field":
                    field=fields[name]
                    header=field["string"]
                else:
                    header=child.attrib["string"]
                headers.append(header)
            wg.col=len(headers)
            wg.make_header(headers)
            wg.maxw=-1
            def make_line_widgets(line):
                record=line.record
                record.remove_event_listener("change")
                widgets=[]
                i=0
                for child in el:
                    if child.tag=="field":
                        name=child.attrib["name"]
                        field=fields[name]
                        if i==0 and self.type=="tree":
                            wg=LineOpener()
                            wg.line=line
                            wg.field_parent=self.view['field_parent']
                        elif field["type"]=="char":
                            wg=InputChar()
                        elif field["type"]=="integer":
                            wg=InputInteger()
                        elif field["type"]=="float":
                            wg=InputFloat()
                        elif field["type"]=="boolean":
                            wg=InputBoolean()
                        elif field["type"]=="date":
                            wg=InputDate()
                        elif field["type"]=="datetime":
                            wg=InputDatetime()
                        elif field["type"]=="text":
                            wg=InputText()
                        elif field["type"]=="selection":
                            wg=InputSelect()
                        elif field["type"]=="many2one":
                            wg=InputM2O()
                        elif field["type"]=="many2many":
                            wg=InputM2M_list()
                        else:
                            raise Exception("invalid field type: %s"%field["type"])
                        wg.readonly=True
                        wg.update_readonly=False
                        wg.name=name
                        wg.field=field
                        wg.view_attrs=child.attrib
                        wg.view_attrs["colspan"]=1
                    elif child.tag=="button":
                        wg=Button()
                    wg.view_wg=self
                    wg.can_focus=i==0
                    wg.update_can_focus=False
                    wg.set_record(record)
                    widgets.append(wg)
                    i+=1
                record.record_changed()
                return widgets
            wg.make_line_widgets=make_line_widgets
            return wg
        else:
            raise Exception("invalid tag in tree view: "+el.tag)

    def load_view(self):
        if not self.view:
            self.view=rpc_exec(self.parent.model,"fields_view_get",self.view_id or False,"tree",self.parent.context)
        arch=xml.etree.ElementTree.fromstring(self.view["arch"])
        if self.tree:
            self.remove(self.tree)
        self.tree=self.parse(arch,self.view["fields"])
        self.add(self.tree)
        self.tree.maxh=-1
        self.tree.maxw=-1
        self.tree.seps=[[(1,True),(0,False)],[(1,True)]]
        def on_open(line_no,source):
            if self.parent.type=="form":
                if self.parent.view_wg:
                    line=self.tree.lines[line_no]
                    rec=line.record
                    link=LinkPopup()
                    link.record=self.parent.record
                    link.view_wg=self.parent.view_wg
                    link.string=self.parent.field["string"]
                    link.form_mode.view=self.parent.field["views"].get("form")
                    link.form_mode.record=ObjRecord(rec.model)
                    link.form_mode.record.id=rec.id
                    link.form_mode.record.vals=rec.vals.copy()
                    link.form_mode.record.fields=rec.fields.copy()
                    link.model=self.parent.model
                    link.form_mode.load_view()
                    link.form_mode.record.read(link.form_mode.view["fields"])
                    def on_close(save=False):
                        if save:
                            rec.set_vals(link.form_mode.record.vals,link.form_mode.record.fields)
                        root_panel.close_popup(link)
                        root_panel.clear_focus()
                        source.set_focus()
                        source.set_cursor()
                    link.on_close=on_close
                    link.show()
                else:
                    line=self.tree.lines[line_no]
                    rec=line.record
                    self.parent.cur_mode="form"
                    self.parent.mode_wg['form'].record=rec
                    self.parent.load_view()
                    self.parent.read()
                    self.parent.cur_wg=self.parent.mode_wg["form"]
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.clear_focus()
                    root_panel.set_focus()
                    root_panel.set_cursor()
            elif self.parent.type=="tree":
                line=self.tree.lines[line_no]
                res=rpc_exec("ir.values","get","action","tree_but_open",[(self.parent.model,line.record.id)])
                if res:
                    act=res[0][2]
                    action(act["id"],_act=act)
        self.tree.add_event_listener("open",on_open)
        def on_expand(line_no,source):
            parent_line=self.tree.lines[line_no]
            parent_rec=parent_line.record
            child_ids=parent_rec.get_val(self.view["field_parent"])
            if child_ids:
                child_recs=self.read_child_records(child_ids)
                child_lines=[]
                for rec in child_recs:
                    line=ListLine()
                    line.record=rec
                    line.open=False
                    line.depth=parent_line.depth+1
                    child_lines.append(line)
                parent_line.childs=child_lines
        self.tree.add_event_listener("expand",on_expand)

    def read(self):
        if self.type=="tree":
            root_fields={'name':self.view['fields']['name'],self.view['field_parent']: {'type': 'many2many'}}
            ObjRecord.read_list(self.parent.model,self.parent.records,root_fields)
            self.root_list.add_records(self.parent.records)
            self.root_list.on_open(0)
        elif self.type=="form":
            ObjRecord.read_list(self.parent.model,self.parent.records,self.view['fields'])
            self.tree.delete_lines()
            recs=[rec for rec in self.parent.records if not rec.deleted]
            self.tree.add_records(recs)

    def read_child_records(self,ids,context=None):
        new_ids=[id for id in ids if not id in self.rec_child_pool]
        for id in new_ids:
            rec=ObjRecord(self.parent.model,id)
            self.rec_child_pool[id]=rec
        recs=[self.rec_child_pool[id] for id in ids]
        tree_fields=self.view['fields'].copy()
        if self.view['field_parent'] not in tree_fields:
            tree_fields.update({self.view['field_parent']: {'type': 'many2many'}})
        ObjRecord.read_list(self.parent.model,recs,tree_fields,context)
        return recs

class FormMode(ScrollPanel):
    def on_keypress(self,k,source):
        if k==curses.KEY_RIGHT:
            if source==self:
                i=self.commands.index(self.cur_cmd)
                i=(i+1)%len(self.commands)
                self.cur_cmd=self.commands[i]
                root_panel.set_cursor()
        elif k==curses.KEY_LEFT:
            if source==self:
                i=self.commands.index(self.cur_cmd)
                i=(i-1)%len(self.commands)
                self.cur_cmd=self.commands[i]
                root_panel.set_cursor()
        elif k==ord('\n'):
            if source==self:
                if self.cur_cmd=="N":
                    self.cur_mode="form"
                    self.load_view()
                    self.active_id=None
                    self.read()
                    self.cur_wg=self.form_mode
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.clear_focus()
                    self.form_mode.set_focus()
                    root_panel.set_cursor()
                elif self.cur_cmd=="S":
                    ObjRecord.save([self.record]) # XXX
                    self.record.clear()
                    self.record.read(self.view['fields'])
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.clear_focus()
                    self.set_focus()
                    self.set_cursor()
                elif self.cur_cmd=="D":
                    mb=MessageBox()
                    mb.set_title("Confirmation")
                    mb.set_message("Are you sure to remove this record?")
                    mb.set_buttons(["Cancel","OK"])
                    def on_close(string):
                        if string=="OK":
                            pass
                    mb.on_close=on_close
                    mb.show()
                elif self.cur_cmd=="R":
                    self.record.clear()
                    self.record.read(self.view['fields'])
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.clear_focus()
                    self.set_focus()
                    self.set_cursor()
                elif self.cur_cmd=="<":
                    pass
                elif self.cur_cmd==">":
                    pass
                elif self.cur_cmd=="T":
                    self.parent.cur_mode="tree"
                    self.parent.load_view()
                    self.parent.read()
                    self.parent.cur_wg=self.parent.mode_wg["tree"]
                    root_panel.compute()
                    root_panel.draw()
                    root_panel.refresh()
                    root_panel.clear_focus()
                    self.parent.cur_wg.set_focus()
                    self.parent.cur_wg.set_cursor()
                elif self.cur_cmd=="F":
                    pass
                elif self.cur_cmd=="C":
                    mb=MessageBox()
                    mb.show("Error","Calendar view not supported",["OK"])
                elif self.cur_cmd=="G":
                    mb=MessageBox()
                    mb.show("Error","Graph view not supported",["OK"])

    def __init__(self):
        super(FormMode,self).__init__()
        self.borders=[1,1,1,1]
        self.add_event_listener("keypress",self.on_keypress)
        self.form=None
        self.view=None
        self.view_id=None
        self.commands=None
        self.can_focus=False

    def set_commands(self,type,modes,window,add=False):
        self.commands=[]
        self.commands+=[add and "A" or "N","D"]
        if window:
            self.commands+=["S","R"]
        self.commands+=["<",">"]
        self.commands+=[mode[0].upper() for mode in modes]
        self.cur_cmd="F"
        self.can_focus=True

    def draw(self):
        super(FormMode,self).draw()
        if self.commands:
            win=self.window
            s=" ".join(self.commands)
            x=self.x+self.w-len(s)-3
            win.addch(self.y,x,curses.ACS_RTEE)
            x+=1
            win.addstr(self.y,x,s)
            x+=len(s)
            win.addch(self.y,x,curses.ACS_LTEE)

    def set_cursor(self):
        i=self.commands.index(self.cur_cmd)
        x=self.x+self.w-len(self.commands)*2-1+i*2
        screen.move(self.win_y+self.y,self.win_x+x)

    def parse(self,el,fields=None,panel=None,form=None):
        if el.tag=="form":
            wg=Form()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            for child in el:
                self.parse(child,panel=wg,fields=fields,form=wg)
            return wg
        elif el.tag=="label":
            wg=Label()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            panel.add(wg)
            return wg
        elif el.tag=="newline":
            panel.newline()
            return None
        elif el.tag=="separator":
            wg=Separator()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            panel.add(wg)
            return wg
        elif el.tag=="button":
            wg=FormButton()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            panel.add(wg)
            return wg
        elif el.tag=="field":
            field=fields[el.attrib["name"]]
            if not el.attrib.get("nolabel"):
                wg_l=FieldLabel()
                wg_l.view_wg=self
                wg_l.view_attrs=el.attrib
                wg_l.field=field
                wg_l.init_attrs()
                wg_l.set_record(self.record)
                wg_l.colspan_follow=wg_l.colspan-1
                wg_l.colspan=1
                panel.add(wg_l)
            if field["type"]=="char":
                wg=InputChar()
            elif field["type"]=="integer":
                wg=InputInteger()
            elif field["type"]=="float":
                wg=InputFloat()
            elif field["type"]=="boolean":
                wg=InputBoolean()
            elif field["type"]=="date":
                wg=InputDate()
            elif field["type"]=="datetime":
                wg=InputDatetime()
            elif field["type"]=="text":
                wg=InputText()
            elif field["type"]=="selection":
                wg=InputSelect()
            elif field["type"]=="many2one":
                wg=InputM2O()
            elif field["type"]=="one2many":
                model=field["relation"]
                modes=el.attrib.get("view_mode") and el.attrib["view_mode"].split(",") or None
                views=field["views"]
                wg=InputO2M(model,modes=modes,views=views)
                wg.load_view()
            elif field["type"]=="many2many":
                model=field["relation"]
                views=field["views"]
                wg=InputM2M(model,views=views)
                wg.load_view()
            elif field["type"]=="reference":
                wg=InputReference()
            else:
                raise Exception("unsupported field type: %s"%field["type"])
            wg.view_wg=self
            wg.name=el.attrib["name"]
            wg.field=field
            wg.view_attrs=el.attrib
            wg.colspan=2
            wg.init_attrs()
            if not el.attrib.get("nolabel"):
                wg.colspan-=1
            wg.set_record(self.record)
            panel.add(wg)
            return wg
        elif el.tag=="group":
            wg=Group()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            for child in el:
                self.parse(child,fields=fields,panel=wg,form=form)
            panel.add(wg)
            return wg
        elif el.tag=="notebook":
            wg=Notebook()
            wg.view_wg=self
            wg.view_attrs=el.attrib
            wg.init_attrs()
            wg.set_record(self.record)
            wg.borders=[1,1,1,1]
            for elp in el:
                wg_p=Page()
                wg_p.view_attrs=elp.attrib
                wg_p.init_attrs()
                wg_p.set_record(self.record)
                wg.add(wg_p)
                for child in elp:
                    self.parse(child,fields=fields,panel=wg_p,form=form)
            panel.add(wg)
            return wg
        else:
            raise Exception("invalid tag in form view: "+el.tag)

    def load_view(self):
        if self.form:
            return
        if not self.view:
            self.view=rpc_exec(self.parent.model,"fields_view_get",self.view_id or False,"form",self.parent.context)
        arch=xml.etree.ElementTree.fromstring(self.view["arch"])
        self.fields=self.view["fields"]
        if self.form:
            self.remove(self.form)
        if not self.record:
            if self.parent.records:
                self.record=self.parent.records[0]
            else:
                self.record=ObjRecord(self.parent.model)
        self.record.remove_event_listener("change")
        self.form=self.parse(arch,self.view["fields"])
        self.add(self.form)
        self.form.maxh=-1

    def read(self):
        self.record.read(self.view["fields"])

    def write(self):
        pass

class InputO2M(ObjBrowser,Input):
    def on_keypress(self,k,source):
        super(InputO2M,self).on_keypress(k,source)
        if k==ord("\n") and source==self:
            if self.cur_cmd=="N":
                wg=LinkPopup()
                wg.model=self.relation
                wg.string=self.string
                wg.view=self.view
                wg.target_wg=self
                wg.show()

    def on_field_change(self):
        val=self.get_val()
        self.records=val
        self.read()

    def __init__(self,model,modes=None,views=None):
        super(InputO2M,self).__init__(model,modes=modes,views=views)
        self.maxh=8

    def draw(self):
        win=self.window
        super(InputO2M,self).draw()
        x=self.x+1
        win.addch(self.y,x,curses.ACS_RTEE)
        x+=1
        s=" "+self.string+" "
        win.addstr(self.y,self.x+2,s)
        x+=len(s)
        win.addch(self.y,x,curses.ACS_LTEE)

    def load_view(self):
        super(InputO2M,self).load_view()
        self.mode_wg["tree"].tree.seps=[[(0,False)],[(1,True)]]

class InputM2M(ObjBrowser,Input):
    def __init__(self,model,modes=None,views=None):
        super(InputM2M,self).__init__(model,modes=modes,views=views,add=True)
        self.maxh=8
        self.maxw=-1

    def on_field_change(self):
        val=self.get_val()
        self.records=val
        self.read()

    def load_view(self):
        super(InputM2M,self).load_view()
        self.mode_wg["tree"].tree.seps=[[(0,False)],[(1,True)]]

class InputM2M_list(StringInput):
    def on_keypress(self,k,source):
        super(InputM2M_list,self).on_keypress(k,source)
        if k==ord("\n"):
            wg=SearchPopup()
            wg.model=self.field["relation"]
            wg.target_wg=self
            wg.show(self.str_val)

    def val_to_str(self,val):
        if val is False:
            return ""
        return "(%d)"%len(val)

    def _compute_pass1(self):
        if self.readonly:
            self.maxw=len(self.str_val)
        else:
            self.maxw=-1

class SelectBox(ListView):
    def on_open(self,line_no):
        line=self.lines[line_no]
        val=line.record.get_val('code')
        root_panel.remove(self)
        self.on_close(val)

    def on_keypress(self,k,source):
        res=super(SelectBox,self).on_keypress(k,source)
        if res:
            return True
        if k==curses.KEY_DOWN:
            ind=self.get_tabindex()
            i=ind.index(source)
            i=(i+1)%len(ind)
            self.clear_focus()
            ind[i].set_focus()
            ind[i].set_cursor()
        elif k==curses.KEY_UP:
            ind=self.get_tabindex()
            i=ind.index(source)
            i=(i-1)%len(ind)
            self.clear_focus()
            ind[i].set_focus()
            ind[i].set_cursor()
        return True

    def __init__(self):
        super(SelectBox,self).__init__()
        self.col=1
        self.selection={}

    def show(self,y,x,query):
        recs=[]
        for k,v in self.selection:
            rec=ObjRecord(None)
            rec.vals={"name":v,"code":k}
            recs.append(rec)
        self.add_records(recs)
        self.window=screen
        self.win_y=0
        self.win_x=0
        self.borders=[1,1,1,1]
        self._compute_pass1()
        self.h=self.maxh
        self.w=self.maxw
        self.y=y
        self.x=x
        self._compute_pass2()
        self.draw()
        screen.refresh()
        root_panel.clear_focus()
        self.set_focus()
        self.set_cursor()
        root_panel.add(self)

class SearchPopup(Table):
    def __init__(self):
        super(SearchPopup,self).__init__()
        self.col=1
        self.title=Label()
        self.add(self.title)
        self.tree_mode=TreeMode("form")
        self.add(self.tree_mode)
        buttons=Group()
        buttons.col=4
        self.add(buttons)
        btn_new=Button()
        btn_new.string="New"
        buttons.add(btn_new)
        #btn_find=Button()
        #btn_find.string="Find"
        #buttons.add(btn_find)
        btn_cancel=Button()
        btn_cancel.string="Cancel"
        buttons.add(btn_cancel)
        btn_ok=Button()
        btn_ok.string="OK"
        buttons.add(btn_ok)
        self.model=None
        self.records=None
        self.string=""
        self.query=""
        self.context={}

    def on_close(self,ids):
        root_panel.close_popup(self)

    def show(self):
        self.tree_mode.load_view()
        self.tree_mode.tree.listeners["open"]=[]
        def on_open(line_no,source):
            line=self.tree_mode.tree.lines[line_no]
            rec=line.record
            ids=[rec.id]
            self.on_close(ids)
        self.tree_mode.tree.add_event_listener("open",on_open)
        self.title.string="Search: "+self.string
        res=rpc_exec(self.model,"name_search",self.query)
        if len(res)==1:
            id=res[0][0]
            self.on_close([id])
        else:
            self.records=[ObjRecord(self.model,r[0]) for r in res]
            self.tree_mode.read()
            root_panel.show_popup(self)

class LinkPopup(Table):
    def on_close(self,save=False):
        root_panel.close_popup(self)

    def on_ok(self,arg,source):
        self.on_close(save=True)

    def on_cancel(self,arg,source):
        self.on_close(save=False)

    def __init__(self):
        super(LinkPopup,self).__init__()
        self.col=1
        self.title=Label()
        self.add(self.title)
        self.form_mode=FormMode()
        self.add(self.form_mode)
        buttons=Group()
        buttons.col=2
        self.add(buttons)
        btn_cancel=Button()
        btn_cancel.string="Cancel"
        btn_cancel.add_event_listener("push",self.on_cancel)
        buttons.add(btn_cancel)
        btn_ok=Button()
        btn_ok.string="OK"
        btn_ok.add_event_listener("push",self.on_ok)
        buttons.add(btn_ok)
        self.context={}
        self.record=None
        self.view_wg=None

    def show(self):
        self.form_mode.record.set_vals(self.form_mode.record.vals,self.form_mode.record.fields)
        self.title.string="Link: "+self.string
        root_panel.show_popup(self)

class StatusPanel(Table):
    def __init__(self):
        super(StatusPanel,self).__init__()
        self.label=Label()
        self.col=1
        self.add(self.label)

    def set_user(self,user):
        self.user=user
        self.update()

    def update(self):
        self.label.string="%s:%d [%s] %s"%(opts.host,opts.port,dbname,self.user)

class MessageBox(Table):
    def on_close(self,val):
        pass

    def on_push(self,arg,source):
        self.on_close(source.string)
        root_panel.close_popup(self)

    def __init__(self):
        super(MessageBox,self).__init__()
        self.col=1
        self.title=Label()
        self.add(self.title)
        self.content=Group()
        self.content.col=1
        self.content.borders=[1,1,1,1]
        self.content.maxw=-1
        self.add(self.content)
        self.message=Label()
        self.content.add(self.message)
        self.buttons=Group()
        self.buttons.col=2
        self.add(self.buttons)

    def set_buttons(self,buttons):
        for string in buttons:
            wg=Button()
            wg.string=string
            wg.add_event_listener("push",self.on_push)
            self.buttons.add(wg)

    def set_title(self,s):
        self.title.string="Message: "+s

    def set_message(self,s):
        self.message.string=s

    def show(self,title=None,message=None,buttons=None):
        if title:
            self.set_title(title)
        if message:
            self.set_message(message)
        if buttons:
            self.set_buttons(buttons)
        root_panel.show_popup(self)

class RootPanel(DeckPanel):
    def on_keypress(self,k,source):
        if k in (ord("\t"),curses.KEY_DOWN):
            ind=self.get_tabindex()
            i=ind.index(source)
            i=(i+1)%len(ind)
            #log("move down",source,getattr(source,"name",""),"->",ind[i],getattr(ind[i],"name",""))
            self.clear_focus()
            ind[i].set_focus()
            self.set_cursor()
        elif k==curses.KEY_UP:
            ind=self.get_tabindex()
            i=ind.index(source)
            i=(i-1)%len(ind)
            self.clear_focus()
            ind[i].set_focus()
            self.set_cursor()

    def __init__(self):
        super(RootPanel,self).__init__()
        self.main=VerticalPanel()
        self.add(self.main)
        self.windows=TabPanel()
        self.windows.maxh=-1
        self.main.add(self.windows)
        self.status=StatusPanel()
        self.status.maxh=1
        self.main.add(self.status)
        self.add_event_listener("keypress",self.on_keypress)
        self.window=screen
        self.win_y=0
        self.win_x=0

    def new_window(self,act):
        name=act.get("name")
        model=act["res_model"]
        type=act.get("view_type")
        modes=act.get("view_mode") and act["view_mode"].split(",") or None
        domain=act.get("domain") and eval(act["domain"]) or []
        context=act.get("context") and eval(act["context"]) or {}
        view_ids={}
        if act.get("views"):
            for (view_id,mode) in act.get("views"):
                view_ids[mode]=view_id
        if act.get("view_id"):
            view_ids[modes[0]]=act["view_id"][0]
        win=ObjBrowser(model,name=name,type=type,modes=modes,view_ids=view_ids,context=context,window=True)
        new_rec=False
        if modes and modes[0]=="form":
            has_id=False
            for cond in domain:
                if cond[0]=="id":
                    has_id=True
                    break
            if not has_id:
                new_rec=True
        if new_rec:
            rec=ObjRecord(model)
            for cond in domain:
                rec.vals[cond[0]]=cond[2]
            recs=[rec]
        else:
            ids=rpc_exec(model,"search",domain,0,10,False,context)
            recs=[ObjRecord(model,id) for id in ids]
        win.records=recs
        win.maxh=-1
        self.windows.add(win)
        self.windows.set_cur_wg(win)
        win.load_view()
        win.read()
        root_panel.compute()
        root_panel.draw()
        root_panel.refresh()
        root_panel.clear_focus()
        root_panel.set_focus()
        root_panel.set_cursor()

    def set_cursor(self):
        wg_f=self.get_focus()
        if wg_f:
            wg_f.set_cursor()

    def show_popup(self,wg):
        self.add(wg)
        self.cur_wg=wg
        self.compute()
        self.draw()
        self.refresh()
        self.clear_focus()
        self.set_focus()
        self.set_cursor()

    def close_popup(self,wg):
        if wg==self.cur_wg:
            self._childs.pop()
            self.cur_wg=self._childs[-1]
        self.compute()
        self.draw()
        self.refresh()
        self.clear_focus()
        self.set_focus()
        self.set_cursor()

    def compute(self):
        super(RootPanel,self).compute(24,80,0,0)

    def draw(self):
        screen.clear()
        super(RootPanel,self).draw()

    def refresh(self):
        screen.refresh()
        super(RootPanel,self).refresh()

def view_to_s(el,d=0):
    s="  "*d+el.tag
    for k in sorted(el.attrib.keys()):
        v=el.attrib[k]
        s+=" %s=%s"%(k,v)
    for child in el:
        s+="\n"+view_to_s(child,d+1)
    return s

def act_window(act_id,_act=None):
    if _act:
        act=_act
    else:
        act=rpc_exec("ir.actions.act_window","read",[act_id],False)[0]
    root_panel.new_window(act)

def action(act_id,_act=None):
    if _act:
        act=_act
    else:
        act=rpc_exec("ir.actions.actions","read",act_id,["name","type"])
    if act["type"]=="ir.actions.act_window":
        act_window(act_id,_act)
    else:
        raise Exception("Unsupported action type: %s"%act["type"])

def start(stdscr):
    global screen,root_panel,dbg_mode
    screen=stdscr
    screen.keypad(1)
    root_panel=RootPanel()
    user=rpc_exec("res.users","read",uid,["name","action_id","menu_id"])
    root_panel.status.set_user(user["name"])
    if opts.user_pref:
        act_id=rpc_exec("res.users","action_get")
    else:
        act_id=user["action_id"][0]
    action(act_id)
    while 1:
        k=screen.getch()
        if dbg_mode:
            set_trace()
        if k==ord('D'):
            #set_trace()
            dbg_mode=1
        source=root_panel.get_focus()
        if not source:
            raise Exception("could not find key press source widget")
        source.process_event("keypress",k,source)
curses.wrapper(start)
