import asyncio, time, json
from playwright.async_api import async_playwright
B="http://127.0.0.1:8765"; S="/home/claude/clipdesk/samples/out/"
def log(*a): print(*a, flush=True)
async def main():
  async with async_playwright() as p:
    br=await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required"])
    ctx=await br.new_context(viewport={"width":1500,"height":1000}, accept_downloads=True)
    pg=await ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type=="error" and errs.append(m.text))
    await pg.goto(B); await pg.wait_for_selector("#upForm")
    await pg.select_option("#upCreator", label="Guest Speaker"); await pg.select_option("#upKind","speech")
    await pg.set_input_files("#file", S+"synthetic_talk_2m30.mp4"); await pg.click("#upBtn")
    await pg.wait_for_url("**/#/source/**"); await pg.wait_for_selector("#vid", timeout=120000); await pg.wait_for_timeout(1000)
    sid=pg.url.split("/source/")[1].split("/")[0]; log("talk source", sid)
    await pg.set_input_files("#trFile", S+"synthetic_talk_2m30.srt"); await pg.wait_for_timeout(2500)
    await pg.wait_for_selector("#findText")
    log("transcript badge:", (await pg.inner_text(".wshead .muted.small"))[:160])
    await pg.fill("#findText","Find the story about the first failed business and keep the lesson."); await pg.click("#findForm button.primary")
    await pg.wait_for_selector(".result", timeout=60000); await pg.wait_for_timeout(800)
    log("RESULTS:\n", await pg.inner_text("#results"))
    await pg.click(".result"); await pg.wait_for_selector("#vprev"); await pg.wait_for_timeout(800)
    await pg.evaluate("S.ws.video.currentTime = 78.0"); await pg.wait_for_timeout(1500)
    await (await pg.query_selector(".editor")).screenshot(path="/tmp/e2e/05_speech_editor.png")
    # no-match request
    await pg.fill("#findText","Find the part about scuba diving"); await pg.click("#findForm button.primary"); await pg.wait_for_timeout(3500)
    log("NO-MATCH:", (await pg.inner_text("#results"))[:300].replace("\n"," | "))
    # ---- manual path on a fresh load (no clip selected)
    await pg.goto(B+f"/#/source/{sid}"); await pg.wait_for_selector("#vid"); await pg.wait_for_timeout(800)
    await pg.click("h1"); await pg.keyboard.press("g"); await pg.keyboard.type("1:30"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(600)
    log("after goto: playhead", await pg.evaluate("S.ws.video.currentTime.toFixed(2)"), "sel", await pg.input_value("#selS"), await pg.input_value("#selE"))
    # typing shortcuts inside a field must not trigger: type 'a' into find box
    await pg.fill("#findText",""); await pg.click("#findText"); await pg.keyboard.type("are"); 
    log("typing 'are' in textarea kept text:", await pg.input_value("#findText"))
    await pg.click("h1")
    await pg.fill("#selS","1:24"); await pg.press("#selS","Enter"); await pg.fill("#selE","1:20"); await pg.press("#selE","Enter"); await pg.wait_for_timeout(300)
    log("invalid order msg:", await pg.inner_text("#selErr"), "| mkClip disabled:", await pg.is_disabled("#mkClip"))
    await pg.fill("#selE","1:24.5+50"); await pg.press("#selE","Enter")
    await pg.fill("#selE","2:20"); await pg.press("#selE","Enter"); await pg.wait_for_timeout(200)
    log("too long msg:", await pg.inner_text("#selErr"))
    await pg.fill("#selE","1:46"); await pg.press("#selE","Enter"); await pg.wait_for_timeout(300)
    await pg.click("#mkClip"); await pg.wait_for_selector("#vprev"); await pg.wait_for_timeout(1000)
    log("manual clip created:", await pg.input_value("#selS"), await pg.input_value("#selE"))
    # move both boundaries with the detail handles
    for hid, dx in (("#hS", 40), ("#hE", -60)):
      b=await pg.locator(hid).bounding_box(); x=b["x"]+b["width"]/2; y=b["y"]+b["height"]/2
      await pg.mouse.move(x,y); await pg.mouse.down(); await pg.mouse.move(x+dx,y,steps=6); await pg.mouse.up(); await pg.wait_for_timeout(400)
    await pg.wait_for_timeout(1500)
    s_after, e_after = await pg.input_value("#selS"), await pg.input_value("#selE")
    log("after dragging handles:", s_after, e_after)
    # replay selection
    await pg.click("h1"); await pg.keyboard.press("p"); await pg.wait_for_timeout(600)
    log("replaying from", await pg.evaluate("S.ws.video.currentTime.toFixed(2)"), "playSel", await pg.evaluate("S.ws.playSel"))
    await pg.evaluate("S.ws.video.pause()")
    # edit first caption
    caps = await pg.query_selector_all(".cap input"); log("caption lines in clip:", len(caps), [await c.input_value() for c in caps][:4])
    await caps[0].fill("I put ALL my savings into it"); await pg.wait_for_timeout(1500)
    # approve with keyboard, then refresh
    await pg.click("h1"); await pg.keyboard.press("a"); await pg.wait_for_timeout(1500)
    clip_url = pg.url
    await pg.reload(); await pg.wait_for_selector("#vprev"); await pg.wait_for_timeout(1500)
    log("after refresh:", clip_url == pg.url, "| status:", await pg.inner_text(".editor .panel .badge"), "| sel:", await pg.input_value("#selS"), await pg.input_value("#selE"),
        "| cap0:", await (await pg.query_selector(".cap input")).input_value())
    await pg.evaluate("S.ws.video.currentTime = S.ws.rev.start + 0.3"); await pg.wait_for_timeout(1200)
    await (await pg.query_selector(".vwrap")).screenshot(path="/tmp/e2e/06_manual_live_preview.png")
    async with pg.expect_download(timeout=240000) as dl:
        await pg.click("h1"); await pg.keyboard.press("e")
    d=await dl.value; await d.save_as("/tmp/e2e/speech_manual_final.mp4"); log("downloaded", d.suggested_filename)
    k = await pg.evaluate("S.ws.clip"); log("CLIP", k["id"], "rev", k["approved_rev"], k["rev"]["start"], k["rev"]["end"], json.dumps(k["rev"]["captions"])[:400])
    # approve the discovered one too, then queue ZIP export
    await pg.goto(B+"/#/queue"); await pg.wait_for_selector("#qList"); await pg.wait_for_timeout(800)
    await pg.screenshot(path="/tmp/e2e/07_queue.png")
    log("queue pending rows:", len(await pg.query_selector_all(".qrow")))
    await pg.click("h1"); await pg.keyboard.press("j"); await pg.keyboard.press("k"); await pg.wait_for_timeout(500)
    sel_title = await pg.inner_text("#qDetail h2"); log("queue selected:", sel_title)
    await pg.keyboard.press("a"); await pg.wait_for_timeout(1500)
    await pg.keyboard.press("u"); await pg.wait_for_timeout(1500)
    await pg.select_option("#qStatus","pending"); await pg.wait_for_timeout(800)
    log("after approve+undo, still pending:", sel_title in await pg.inner_text("#qList"))
    # approve it for real
    rows = await pg.query_selector_all(".qrow")
    for r in rows:
      if sel_title in (await r.inner_text()): await r.click(); break
    await pg.wait_for_timeout(600); await pg.click("#qA"); await pg.wait_for_timeout(1200)
    await pg.select_option("#qStatus","approved"); await pg.wait_for_timeout(800)
    log("approved rows:", [ (await r.inner_text()).replace("\n"," ")[:90] for r in await pg.query_selector_all(".qrow")])
    async with pg.expect_download(timeout=400000) as dl:
        await pg.click("#qExport")
    d=await dl.value; await d.save_as("/tmp/e2e/export.zip"); log("zip", d.suggested_filename)
    await pg.wait_for_timeout(1500); await pg.screenshot(path="/tmp/e2e/08_queue_after_export.png")
    log("errors:", errs)
    await br.close()
asyncio.run(main())
