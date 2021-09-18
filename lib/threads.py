from .utils import make_http_socket, shutdown_socket, make_embed, send_webhook
from json import loads as json_loads
from zlib import decompress
from socket import gethostbyname
import re

GROUP_API = "groups.roblox.com"
GROUP_API_ADDR = (gethostbyname(GROUP_API), 443)
BATCH_GROUP_PATTERN = re.compile(b'\{"id":(\d+),.{25}.+?,"owner":(.)')
BATCH_GROUP_REQUEST = (
    b"GET /v2/groups?groupIds=%b HTTP/1.1\n"
    b"Host:groups.roblox.com\n"
    b"Accept-Encoding:deflate\n"
    b"\n")
SINGLE_GROUP_REQUEST = (
    b"GET /v1/groups/%b HTTP/1.1\n"
    b"Host:groups.roblox.com\n"
    b"\n")

def thread_func(thread_num, worker_num,
                thread_barrier, thread_event,
                check_counter, proxy_iter,
                gid_ranges, gid_cutoff, gid_chunk_size,
                webhook_url, timeout):
    gid_list = [
        str(gid).encode()
        for gid_range in gid_ranges
        for gid in range(*gid_range)
    ]
    gid_list_len = len(gid_list)
    gid_list_idx = 0
    gid_tracked = set()

    thread_barrier.wait()
    thread_event.wait()

    while True:
        proxy_addr = next(proxy_iter) if proxy_iter else None

        try:
            sock = make_http_socket(GROUP_API_ADDR, timeout, proxy_addr, hostname=GROUP_API)
        except:
            continue
        
        while True:
            if gid_chunk_size > gid_list_len:
                return

            gid_chunk = [
                gid_list[(gid_list_idx := gid_list_idx + 1) % gid_list_len]
                for _ in range(gid_chunk_size)
            ]

            try:
                # Request batch group details.
                sock.send(BATCH_GROUP_REQUEST % b",".join(gid_chunk))
                resp = sock.recv(1048576)
                if not resp.startswith(b"HTTP/1.1 200 OK"):
                    break
                resp = resp.partition(b"\r\n\r\n")[2]
                while resp[-1] != 0:
                    resp += sock.recv(1048576)
                owner_status = {
                    m[0]: m[1] == b"{"
                    for m in BATCH_GROUP_PATTERN.findall(decompress(resp, -15))
                }

                for gid in gid_chunk:
                    if gid not in owner_status:
                        # Details for this group weren't included in the response.
                        if not gid_cutoff or gid_cutoff > int(gid):
                            # Group is outside of cut-off range.
                            # Assume it doesn't exist and ignore it in the future.
                            gid_list.remove(gid)
                            gid_list_len -= 1
                        continue
                    
                    if gid not in gid_tracked:
                        if owner_status[gid]:
                            # Group has an owner and this is the first time it's been checked.
                            # Mark it as tracked.
                            gid_tracked.add(gid)
                        else:
                            # Group doesn't have an owner, and this is only the first time it's been checked.
                            # Assume that it's locked or manual-approval only, and ignore it in the future.
                            gid_list.remove(gid)
                            gid_list_len -= 1
                        continue

                    if owner_status[gid]:
                        # Group has an owner and it's been checked previously.
                        # Skip to next group in the batch.
                        continue

                    # Group is marked as tracked and doesn't have an owner.
                    # Request extra details and determine if it's claimable.
                    sock.send(SINGLE_GROUP_REQUEST % gid)
                    resp = sock.recv(1024 ** 2)
                    if not resp.startswith(b"HTTP/1.1 200 OK"):
                        break
                    group_info = json_loads(resp.partition(b"\r\n\r\n")[2])

                    if (
                        not group_info["publicEntryAllowed"]
                        or group_info["owner"]
                        or "isLocked" in group_info
                    ):
                        # Group cannot be claimed, ignore it in the future.
                        gid_list.remove(gid)
                        gid_list_len -= 1
                        continue

                    print(" | ".join([
                        f"https://www.roblox.com/groups/{gid.decode()}",
                        f"{group_info['memberCount']} members",
                        group_info["name"]
                    ]))

                    if webhook_url:
                        send_webhook(webhook_url, embeds=(make_embed(group_info),))

                    # Ignore group in the future.
                    gid_list.remove(gid)
                    gid_list_len -= 1

                # Let the counter know that gid_chunk_size groups were checked.
                check_counter.add(gid_chunk_size)

            except KeyboardInterrupt:
                exit()
            
            except Exception as err:
                break
            
        shutdown_socket(sock)
