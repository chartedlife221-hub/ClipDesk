import asyncio, json
from playwright.async_api import async_playwright
B="http://127.0.0.1:8765"
def log(*a): print(*a, flush=True)
async def main():
  async with async_playwright() as p:
    br=await p.chromium.launch(executable_path="/usr/bin/google-chrome")
    ctx=await br.new_context(viewport={"width":1500,"height":1000}, accept_downloads=True); pg=await ctx.new_page()
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type=="error" and errs.append(m.text))
    # reset the two clips approved by the probe scripts back to pending so the queue reflects the walkthrough
    for cid in ("clp_ad1be696c8","clp_690563b906","clp_4145a1587a"):
      await pg.request.post(B+f"/api/clips/{cid}/status", data=json.dumps({"status":"pending"}), headers={"content-type":"application/json"})
    await pg.goto(B+"/"); await pg.wait_for_selector(".sources"); await pg.click("a[data-nav=queue]"); await pg.wait_for_selector("#qList"); await pg.wait_for_timeout(1200)
    await pg.screenshot(path="/tmp/e2e/07_queue.png")
    rows=[(await r.inner_text()).replace("\n"," ")[:100] for r in await pg.query_selector_all(".qrow")]; log("pending rows:", len(rows)); [log("  ",r) for r in rows]
    # select the coffee-cart suggestion with J/K and approve with A, undo with U
    await pg.click("h1")
    for i in range(len(rows)):
      if "coffee cart" in (await pg.inner_text("#qDetail h2")): break
      await pg.keyboard.press("j"); await pg.wait_for_timeout(500)
    title=await pg.inner_text("#qDetail h2"); log("selected:", title)
    await pg.keyboard.press("a"); await pg.wait_for_timeout(1500)
    log("pending after A:", len(await pg.query_selector_all(".qrow")))
    await pg.keyboard.press("u"); await pg.wait_for_timeout(1500)
    log("pending after U:", len(await pg.query_selector_all(".qrow")), "| contains it again:", "coffee cart" in await pg.inner_text("#qList"), "| selected:", await pg.inner_text("#qDetail h2"))
    for i in range(6):
      if "coffee cart" in (await pg.inner_text("#qDetail h2")): break
      await pg.keyboard.press("j"); await pg.wait_for_timeout(500)
    await pg.keyboard.press("a"); await pg.wait_for_timeout(1500)
    await pg.select_option("#qStatus","approved"); await pg.wait_for_timeout(1000)
    rows=[(await r.inner_text()).replace("\n"," ")[:100] for r in await pg.query_selector_all(".qrow")]; log("approved:"); [log("  ",r) for r in rows]
    async with pg.expect_download(timeout=600000) as dl:
        await pg.click("#qExport")
    d=await dl.value; await d.save_as("/tmp/e2e/export.zip"); log("zip:", d.suggested_filename)
    await pg.wait_for_timeout(1500); await pg.screenshot(path="/tmp/e2e/08_queue_after_export.png")
    log("errors:", errs)
    await br.close()
asyncio.run(main())
