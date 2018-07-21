# -*- coding: utf-8 -*-
from aiohttp import web
import asyncio
import json,sys,os
import webbrowser
import traceback
import uuid
import contextlib

__version__="0.1.7"

try:
    os.chdir(sys._MEIPASS)  # when freezed with pyinstaller ;-)
except:
    try:
        os.chdir(os.path.split(sys.argv[0])[0])
    except:
        pass

clients=[] #<- for saving clients cnx
exposed={} #<- for saving exposed methods
application = None
closeIfSocketClose=False
size=None
isLog=True

def log(*a):
    if isLog: print(*a)

async def as_emit(event,args,exceptMe=None):
    global clients
    for ws in clients:
        if id(ws) != id(exceptMe):
            log("  < emit event '%s' : %s" % (event,args))
            await ws.send_str( json.dumps( dict(event=event,args=args) ))

def emit(event,args):   # sync version of emit for py side !
    asyncio.ensure_future( as_emit( event, args) )

async def handleWeb(request): # serve all statics from web folder
    file = './web/'+request.match_info.get('path', "index.html")
    if os.path.isfile(file):
        log("- serve static file",file)
        return web.FileResponse(file)
    else:
        log("! 404 on",file)
        return web.Response(status=404,body="file not found")

async def handleJs(request): # serve the JS
    global size
    log("- serve wuy.js",size and ("(with resize to "+str(size)+")") or "")

    name=os.path.basename(sys.argv[0])
    if "." in name: name=name.split(".")[0]
    js="""
document.addEventListener("DOMContentLoaded", function(event) {
    %s
    %s
},true)

function setupWS( cbCnx ) {

    var ws=new WebSocket( window.location.origin.replace("http","ws")+"/ws" );

    ws.onmessage = function(evt) {
      var r = JSON.parse(evt.data);
      if(r.uuid) // that's a response from call py !
          document.dispatchEvent( new CustomEvent('wuy-'+r.uuid,{ detail: r} ) );
      else if(r.event){ // that's an event from anywhere !
          document.dispatchEvent( new CustomEvent(r.event,{ detail: r.args } ) );
      }
    };

    ws.onclose = function(evt) {
        console.log("disconnected");
        cbCnx(null);
        setTimeout( function() {setupWS(cbCnx)}, 1000);
    };
    ws.onerror = function(evt) {cbCnx(null);};
    ws.onopen=function(evt) {
        console.log("Connected",evt)
        cbCnx(ws);
    }

    return ws;
}

var wuy = new Proxy( {
        _ws: setupWS( ws=>{wuy._ws = ws} ),
        on: function( evt, callback ) {     // to register an event on a callback
            document.addEventListener(evt,function(e) { callback(e.detail) })
        },
        emit: function( evt, data) {        // to emit a event to all clients (except me), return a promise when done
            return wuy._call("emit",evt,data)
        },
        _call: function( _ ) {               
            var args=Array.prototype.slice.call(arguments);

            var cmd={
                command:    args.shift(0),
                args:       args,
            };
            cmd.uuid = cmd.command+"-"+Math.random().toString(36).substring(2); // stamp the exchange, so the callback can be called back (thru customevent)
            if(wuy._ws) {
                wuy._ws.send( JSON.stringify(cmd) );

                return new Promise( function (resolve, reject) {
                    document.addEventListener('wuy-'+cmd.uuid, function handler(x) {
                        this.removeEventListener('wuy-'+cmd.uuid, handler);
                        var x=x.detail;
                        if(x && x.result)
                            resolve(x.result)
                        else
                            reject(x.error)
                    });
                })
            }
            else 
                return new Promise( function (resolve, reject) {
                    reject("not connected");
                })

        }
    },
    {
        get: function(target, propKey, receiver){   // proxy: wuy.method(args) ==> wuy._call( method, args )
            if(target.hasOwnProperty(propKey))
                return target[propKey];
            else
                return function() {
                    var args=[propKey].concat( Array.prototype.slice.call(arguments) )
                    return target._call.apply( target, args);
                }
        }
    },
);
""" % (size and "window.resizeTo(%s,%s);"%(size[0],size[1]) or "",'document.title="%s";'%name)
    return web.Response(status=200,text=js)

async def wshandle(request):
    global clients
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    clients.append(ws)

    async for msg in ws:
        if msg.type == web.WSMsgType.text:
            try:
                o=json.loads( msg.data )
                log("> RECEPT",o)
                if o["command"] == "emit":
                    event, *args = o["args"]
                    await as_emit( event, args, ws) # emit to everybody except me
                    r=dict(result = args)           # but return the same content sended, thru the promise
                else:
                    r=dict(result = exposed[o["command"]]( *o["args"] ) )
            except Exception as e:
                r=dict(error = str(e), traceback=traceback.format_exc() )

            if "uuid" in o: r["uuid"]=o["uuid"]

            log("< return",r)
            await ws.send_str( json.dumps( r ) )
        elif msg.type == web.WSMsgType.close:
            break

    clients.remove( ws )
    # for i in clients:
    #     print(dir(i))
    if closeIfSocketClose: exit()
    return ws


def getBro():
    for b in ['google-chrome','chrome',"chromium","chromium-browser","mozilla","firefox"]:
        try:
            return webbrowser.get(b)
        except webbrowser.Error:
            pass

def startApp(url):
    b=getBro()
    if b:
        if "mozilla" in str(b).lower():
            b._invoke(["--new-window",url],0,0) #window.close() won't work ;-(
        else:
            b._invoke(["--app="+url],1,1)
        return True


################################################# exposed methods vv
def expose( f ):    # decorator !
    global exposed
    exposed[f.__name__]=f
    return f

def exit():         # exit method
    for task in asyncio.Task.all_tasks():
        task.cancel()

    loop = asyncio.get_event_loop()
    loop.stop()
    
    asyncio.set_event_loop(asyncio.new_event_loop())    # renew, so multiple start are possibles

def start(port=8080,app=None,log=True):   # start method (app can be True, (width,size), ...)
    global closeIfSocketClose,size,isLog
    isLog=log

    # create startpage if not present
    startpage="./web/index.html"
    if not os.path.isfile(startpage):
        if not os.path.isdir(os.path.dirname(startpage)):
            os.makedirs(os.path.dirname(startpage))
        with open(startpage,"w+") as fid:
            fid.write('''<script src="wuy.js"></script>\n''')
            fid.write('''Hello Wuy'rld ;-)''')
        print("Create %s, just edit it" % startpage)

    if app:
        closeIfSocketClose=startApp("http://localhost:%s/?%s"% (port,uuid.uuid4().hex))
        if type(app)==tuple and len(app)==2:
            size=app

    application=web.Application()
    application.add_routes([
            web.get('/ws', wshandle),

            web.get('/wuy.js', handleJs),

            web.get('/', handleWeb),
            web.get('/{path}', handleWeb),
    ])
    web.run_app(application,port=port)

if __name__=="__main__":
    log("test",42)
