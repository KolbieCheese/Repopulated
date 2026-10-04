from pathlib import Path
import socket
import sys
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_coop import read_initial_world,send_message


class NativeStartupTests(unittest.TestCase):
    def test_pings_do_not_extend_initial_world_deadline(self):
        client,peer=socket.socketpair();stop=threading.Event()
        reader=client.makefile('rwb');writer=peer.makefile('wb')
        def ping():
            try:
                while not stop.is_set():
                    send_message(writer,{'type':'ping'});stop.wait(0.02)
            except OSError:pass
        thread=threading.Thread(target=ping,daemon=True);thread.start();started=time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError,'initial world'):read_initial_world(client,reader,timeout_seconds=0.15)
            self.assertLess(time.monotonic()-started,1)
        finally:
            stop.set();thread.join(timeout=1);reader.close();writer.close();client.close();peer.close()

    def test_host_failure_is_reported_before_client_bootstrap(self):
        client,peer=socket.socketpair();reader=client.makefile('rwb');writer=peer.makefile('wb')
        try:
            send_message(writer,{'type':'error','message':'Host native instrumentation failed'})
            with self.assertRaisesRegex(RuntimeError,'Host native instrumentation failed'):read_initial_world(client,reader)
        finally:reader.close();writer.close();client.close();peer.close()


if __name__=='__main__':unittest.main()
