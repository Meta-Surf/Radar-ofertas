"""Fotos são fixtures binárias; download e rebranding sempre simulados."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fotos_revisao import save_revision_photo


class RevisionPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.message=SimpleNamespace(id=7)

    async def save(self,revision,content=b'fixture',rebrand=None):
        async def download(message,file):
            Path(file).write_bytes(content);return file
        return await save_revision_photo(self.message,-1,revision,self.root,download,rebrand)

    async def test_new_native_image_cannot_overwrite_old_selected_image(self):
        old=await self.save('a'*64,b'old')
        new=await self.save('b'*64,b'new')
        self.assertNotEqual(old,new);self.assertEqual(old.read_bytes(),b'old');self.assertEqual(new.read_bytes(),b'new')

    async def test_same_revision_is_immutable_and_does_not_download_again(self):
        old=await self.save('a'*64,b'old');download=AsyncMock(side_effect=AssertionError('duplicate download'))
        result=await save_revision_photo(self.message,-1,'a'*64,self.root,download)
        self.assertEqual(result,old);download.assert_not_called()

    async def test_file_becomes_visible_only_after_download_and_rebranding(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def brand(path):
            entered.set();await release.wait();path.write_bytes(b'branded')
        task=asyncio.create_task(self.save('a'*64,b'raw',brand))
        await asyncio.wait_for(entered.wait(),2)
        self.assertEqual(list(self.root.glob('-1_*.jpg')),[])
        release.set();final=await task;self.assertEqual(final.read_bytes(),b'branded')
        self.assertEqual(list(self.root.glob('.capture-*')),[])

    async def test_failed_new_rebranding_preserves_previous_revision(self):
        old=await self.save('a'*64,b'old')
        async def fail(path):raise RuntimeError('injected')
        with self.assertRaises(RuntimeError):await self.save('b'*64,b'raw',fail)
        self.assertEqual(old.read_bytes(),b'old');self.assertEqual(len(list(self.root.iterdir())),1)

    async def test_cancellation_does_not_publish_partial_file_or_remove_old(self):
        old=await self.save('a'*64,b'old')
        async def cancel(path):raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):await self.save('b'*64,b'raw',cancel)
        self.assertEqual(old.read_bytes(),b'old');self.assertEqual(len(list(self.root.iterdir())),1)

    async def test_empty_download_and_invalid_revision_never_publish_file(self):
        self.assertIsNone(await self.save('a'*64,b''))
        with self.assertRaises(ValueError):await self.save('../unsafe')
        self.assertEqual(list(self.root.iterdir()),[])
