"""Role 2A: actual loopback multipart/storage; no STE, image decoder or provider."""
import base64
import copy
import http.client
import io
import json
import os
import socket
import struct
import threading
import types
import unittest
import zipfile
import zlib
from unittest import mock

import test_slicer_settings  # Adds the existing plugin import root.
from AIAssistant import uploads
from AIAssistant.agent.service import AgentService
from AIAssistant.server import create_server
from AIAssistant.uploads import FileStore, UploadError


STL = b"solid part\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid part\n"
OBJ = b"# comment\r\no same-name\r\nv 0 0 0\r\nv 1 0 0\r\nv 0 1 0\r\nf 1 2 3\r\n"
def png_chunk(kind, value):
    return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
PNG = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
       png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + png_chunk(b"IEND", b""))
# Structurally valid JPEG markers; full entropy decoding is outside basic upload validation.
JPEG = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x00\xff\xd9"


def three_mf(extra=None, model=None):
    if model is None:
        model = (b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
                 b'<object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/>'
                 b'<vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices>'
                 b'<triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object></resources>'
                 b'<build><item objectid="1"/></build></model>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("_rels/.rels", b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        archive.writestr("3D/3dmodel.model", model)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def multipart(data=STL, name="part.stl", boundary="Boundary-task2A", field="file", media="application/octet-stream", extra=b"", final_crlf=True):
    header = ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + field + '"; filename="' + name +
              '"\r\nContent-Type: ' + media + '\r\n\r\n').encode("utf-8")
    ending = ("\r\n--" + boundary + "--" + ("\r\n" if final_crlf else "")).encode("ascii")
    return header + data + extra + ending, "multipart/form-data; boundary=" + boundary


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.store = FileStore()
        self.addCleanup(self.close_store)

    def close_store(self):
        self.store.close()
        self.assertTrue(self.store.wait_closed(2))

    def upload(self, data=STL, name="part.stl", **kwargs):
        body, content_type = multipart(data, name, **kwargs)
        return self.store.upload(io.BytesIO(body), len(body), content_type)

    def assert_status(self, status, callback):
        with self.assertRaises(UploadError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        return caught.exception

    def test_all_formats_bytes_mime_unique_ids_and_private_names(self):
        binary = b"solid binary header".ljust(80, b" ") + struct.pack("<I", 1) + bytes(50)
        samples = [(STL, "деталь.stl", "model/stl"), (binary, "same.STL", "model/stl"),
                   (OBJ, "same.obj", "model/obj"), (three_mf(), "project.3mf", "model/3mf"),
                   (PNG, "picture.png", "image/png"), (JPEG, "picture.jpeg", "image/jpeg")]
        seen = set()
        for data, name, mime in samples:
            with self.subTest(name=name):
                metadata = self.upload(data, name, media="text/html")  # Client MIME is not authoritative.
                self.assertEqual(metadata["mime_type"], mime)
                self.assertEqual(metadata["name"], name)
                self.assertEqual(metadata["size_bytes"], len(data))
                self.assertNotIn(metadata["file_id"], seen)
                seen.add(metadata["file_id"])
                self.assertNotIn("path", metadata)
                self.assertNotIn(self.store._directory, json.dumps(metadata))
                if metadata["kind"] == "image":
                    actual_mime, actual = self.store.read_image(metadata["file_id"])
                    self.assertEqual((actual_mime, actual), (mime, data))
                else:
                    with self.store.model_path(metadata["file_id"]) as path:
                        self.assertNotEqual(os.path.basename(path), name)
                        with open(path, "rb") as stream:
                            self.assertEqual(stream.read(), data)
        self.assertEqual(len(self.store._records), 6)

    def test_safe_display_name_no_paths_and_registry_not_user_json(self):
        for name in ("../part.stl", "C:\\secret\\part.stl", "/part.stl", "bad:name.stl", "bad\x00.stl", "part.stl "):
            self.assert_status(400, lambda: self.upload(name=name))
        self.assert_status(415, lambda: self.upload(name="run.exe"))
        good = self.upload()
        copy_metadata = self.store.metadata(good["file_id"])
        copy_metadata["path"] = "C:/private.stl"
        copy_metadata["kind"] = "image"
        self.assertNotIn("path", self.store.metadata(good["file_id"]))
        self.assertEqual(self.store.metadata(good["file_id"])["kind"], "model")
        for unknown in ("C:/private.stl", "../part.stl", "file-missing", {}, None):
            self.assert_status(404, lambda: self.store.model_path(unknown))
        self.assert_status(415, lambda: self.store.read_image(good["file_id"]))

    def test_wrong_multipart_fields_duplicate_parts_or_trailer_never_register(self):
        self.assert_status(400, lambda: self.upload(field="other"))
        self.assert_status(400, lambda: self.upload(extra=b'\r\n--Boundary-task2A\r\nContent-Disposition: form-data; name="file"; filename="second.stl"\r\n\r\nsecond'))
        body, content_type = multipart()
        for damaged in (body[:-3], body + b"epilogue", body.replace(b' name="file";', b' name="file"; name="other";'),
                        body.replace(b"Content-Type:", b"Extra: value\r\nContent-Type:")):
            self.assert_status(400, lambda: self.store.upload(io.BytesIO(damaged), len(damaged), content_type))
        self.assertEqual(self.store._records, {})
        if self.store._directory:
            self.assertEqual(os.listdir(self.store._directory), [])
        self.assert_status(415, lambda: self.store.upload(io.BytesIO(body), len(body), "application/json"))
        self.assert_status(400, lambda: self.store.upload(io.BytesIO(body), len(body), "multipart/form-data; boundary=x; boundary=y"))
        self.assert_status(400, lambda: self.store.upload(io.BytesIO(body), len(body) + 5, content_type))
        self.assertEqual(self.store._used, 0)

    def test_boundary_across_chunks_optional_final_crlf_and_no_unbounded_reads(self):
        body, content_type = multipart(final_crlf=False)
        class TinyReads(io.BytesIO):
            def read(self, size=-1):
                if size < 0:
                    raise AssertionError("Bounded reads only")
                return super().read(min(7, size))
        result = self.store.upload(TinyReads(body), len(body), content_type)
        self.assertEqual(result["size_bytes"], len(STL))
        data = STL + b"\r\n--Boundary-task2A\r\nfile"
        body, content_type = multipart(data)
        self.assert_status(400, lambda: self.store.upload(TinyReads(body), len(body), content_type))

    def test_file_wrapper_and_total_quota_limits_keep_accounting_and_remove_partial(self):
        with mock.patch.object(uploads, "MODEL_BYTES", 40):
            self.assert_status(413, lambda: self.upload())
        with mock.patch.object(uploads, "IMAGE_BYTES", 40):
            self.assert_status(413, lambda: self.upload(PNG, "p.png"))
        body, content_type = multipart()
        self.assert_status(413, lambda: self.store.upload(io.BytesIO(), uploads.MODEL_BYTES + uploads.WRAPPER_BYTES + 1, content_type))
        large_header = body.replace(b"application/octet-stream", b"x" * uploads.WRAPPER_BYTES)
        self.assert_status(413, lambda: self.store.upload(io.BytesIO(large_header), len(large_header), content_type))
        self.store._total_limit = len(STL)
        first = self.upload()
        self.assert_status(413, lambda: self.upload())
        self.assertEqual(self.store._used, len(STL))
        self.assertEqual(list(self.store._records), [first["file_id"]])
        self.assertEqual(len(os.listdir(self.store._directory)), 1)

    def test_exclusive_name_and_id_collisions_never_overwrite_or_unlink_existing(self):
        first = self.upload()
        with self.store.model_path(first["file_id"]) as path:
            physical_id = os.path.splitext(os.path.basename(path))[0]
            with mock.patch.object(uploads.uuid, "uuid4", return_value=types.SimpleNamespace(hex=physical_id)), \
                    self.assertLogs("AIAssistant.uploads", level="ERROR"):
                self.assert_status(500, lambda: self.upload())
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), STL)
        actual_uuid = uploads.uuid.uuid4()
        sequence = [actual_uuid, types.SimpleNamespace(hex=first["file_id"][5:])]
        with mock.patch.object(uploads.uuid, "uuid4", side_effect=sequence):
            self.assert_status(500, lambda: self.upload())
        self.assertEqual(self.store.metadata(first["file_id"]), first)
        self.assertEqual(self.store._used, len(STL))
        self.assertEqual(len(os.listdir(self.store._directory)), 1)

    def test_content_mismatch_bad_images_obj_and_both_stl_variants(self):
        for data, name in ((b"html", "p.png"), (JPEG, "p.png"), (PNG[:-2], "p.png"),
                           (PNG[:-1] + b"\x00", "p.png"),
                           (b"\xff\xd8\xff\xd9", "p.jpg"), (b"arbitrary", "p.stl"),
                           (b"v 0 0 0\nf 1 1 1\x00", "p.obj")):
            self.assert_status(415 if not name.endswith("obj") else 400, lambda: self.upload(data, name))
        self.assertEqual(self.store._records, {})

    def test_zip_structure_bomb_paths_dtd_and_crc_rejected_without_extract(self):
        damaged = bytearray(three_mf())
        crc_offset = damaged.index(b"PK\x01\x02") + 16
        damaged[crc_offset] ^= 1
        bad_deflate = bytearray(three_mf())
        name_size, extra_size = struct.unpack("<HH", bad_deflate[26:30])
        bad_deflate[30 + name_size + extra_size] = 7  # Reserved DEFLATE block type -> zlib.error.
        cases = [(b"PK fake", 400), (three_mf(extra={"../outside.stl": b"bad"}), 400),
                 (bytes(damaged), 400), (bytes(bad_deflate), 400),
                 (three_mf(model=b'<!DOCTYPE model [<!ENTITY x "a">]><model/>'), 400),
                 (three_mf(model=b"<unrelated/>"), 400)]
        for data, status in cases:
            self.assert_status(status, lambda: self.upload(data, "p.3mf"))
        with mock.patch.object(uploads, "ZIP_BYTES", 100):
            self.assert_status(413, lambda: self.upload(three_mf(), "p.3mf"))
        with mock.patch.object(uploads, "XML_BYTES", 10):
            self.assert_status(413, lambda: self.upload(three_mf(), "p.3mf"))
        with mock.patch.object(uploads, "ZIP_ENTRIES", 2):
            self.assert_status(413, lambda: self.upload(three_mf(), "p.3mf"))
        self.assertEqual(self.store._records, {})

    def test_close_only_removes_own_files_and_empty_directory(self):
        self.upload()
        directory = self.store._directory
        sentinel = os.path.join(directory, "foreign-test-sentinel.txt")
        with open(sentinel, "wb") as stream:
            stream.write(b"keep")
        try:
            with self.assertLogs("AIAssistant.uploads", level="WARNING"):
                self.store.close()
                self.assertTrue(self.store.wait_closed(1))
            with open(sentinel, "rb") as stream:
                self.assertEqual(stream.read(), b"keep")
            self.assertEqual(os.listdir(directory), ["foreign-test-sentinel.txt"])
        finally:
            os.unlink(sentinel)
            os.rmdir(directory)

    def test_concurrent_busy_and_close_during_incomplete_upload(self):
        entered, release, errors = threading.Event(), threading.Event(), []
        body, content_type = multipart()
        class Blocking(io.BytesIO):
            def read(self, size=-1):
                entered.set()
                release.wait(2)
                return super().read(size)
        def upload():
            try:
                self.store.upload(Blocking(body), len(body), content_type)
            except UploadError as exc:
                errors.append(exc.status)
        thread = threading.Thread(target=upload)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        self.assert_status(409, lambda: self.upload())
        directory = self.store._directory
        self.store.close()
        self.assertFalse(self.store.wait_closed(0.02))
        release.set()
        thread.join(2)
        self.assertEqual(errors, [503])
        self.assertTrue(self.store.wait_closed(1))
        self.assertFalse(os.path.exists(directory))
        self.assertEqual(self.store._records, {})

    def test_cleanup_waits_for_model_lease_and_never_does_io_on_close_caller(self):
        metadata = self.upload()
        lease = self.store.model_path(metadata["file_id"])
        directory, path = self.store._directory, lease.path
        main, original = threading.current_thread(), uploads.os.unlink
        calls = []
        def unlink(value):
            calls.append(threading.current_thread())
            self.assertIsNot(threading.current_thread(), main)
            return original(value)
        with mock.patch.object(uploads.os, "unlink", side_effect=unlink):
            self.store.close()
            self.assertFalse(self.store.wait_closed(0.02))
            self.assertTrue(os.path.isfile(path))
            self.assert_status(503, lambda: self.store.model_path(metadata["file_id"]))
            barrier = threading.Barrier(8)
            def close_same_lease():
                barrier.wait(1)
                lease.close()
            workers = [threading.Thread(target=close_same_lease) for unused in range(8)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())
            lease.close()
            self.assertTrue(self.store.wait_closed(1))
        self.assertTrue(calls)
        self.assertFalse(os.path.exists(directory))
        self.assertEqual(self.store._users, 0)

    def test_lazy_service_and_new_process_do_not_resolve_existing_id(self):
        with mock.patch.object(uploads.tempfile, "mkdtemp", side_effect=AssertionError("No startup IO")):
            service = AgentService()
            service.close()
        good = self.upload()
        other = FileStore()
        self.addCleanup(other.close)
        self.assert_status(404, lambda: other.metadata(good["file_id"]))


class UploadHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = AgentService()
        self.server = create_server(self.service)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertTrue(self.service.file_store.wait_closed(2))

    def request(self, path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        self.addCleanup(connection.close)
        connection.request(method or ("POST" if body is not None else "GET"), path, body, headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode()), response.headers

    def test_real_http_upload_201_bytes_metadata_no_download_and_message_requires_config(self):
        body, content_type = multipart(name="модель.stl")
        code, metadata, headers = self.request("/api/files", body, {"Content-Type": content_type})
        self.assertEqual(code, 201)
        self.assertEqual(metadata["name"], "модель.stl")
        self.assertEqual(metadata["mime_type"], "model/stl")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        for image, name, mime in ((PNG, "photo.png", "image/png"), (JPEG, "photo.jpg", "image/jpeg")):
            image_body, image_content_type = multipart(image, name)
            image_code, image_meta, unused = self.request("/api/files", image_body, {"Content-Type": image_content_type})
            self.assertEqual(image_code, 201)
            self.assertEqual(image_meta["mime_type"], mime)
            self.assertEqual(self.service.file_store.read_image(image_meta["file_id"]), (mime, image))
        with self.service.file_store.model_path(metadata["file_id"]) as path:
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), STL)
        self.assertEqual(self.request("/api/files/" + metadata["file_id"])[0], 404)
        self.assertEqual(self.request("/" + os.path.basename(path))[0], 404)
        status = self.request("/api/status")[1]
        self.assertTrue(status["capabilities"]["attachments"])
        self.assertFalse(status["capabilities"]["downloads"])
        session = self.request("/api/sessions", b"{}", {"Content-Type": "application/json"})[1]
        message = json.dumps({"text": "file", "client_message_id": "attempt", "attachment_ids": [metadata["file_id"]]}).encode()
        code, data, unused = self.request("/api/sessions/" + session["session_id"] + "/messages", message, {"Content-Type": "application/json"})
        self.assertEqual((code, data["error"]["code"]), (503, "llm_not_configured"))

    def test_origin_host_json_limit_missing_length_and_chunked(self):
        body, content_type = multipart()
        for bad in ({"Origin": "http://localhost:1"}, {"Host": "localhost:1"}):
            headers = dict({"Content-Type": content_type}, **bad)
            self.assertEqual(self.request("/api/files", body, headers)[0], 403)
        self.assertEqual(self.request("/api/files", body, {"Content-Type": "application/json"})[0], 415)
        code, data, unused = self.request("/api/files", b"x", {"Content-Type": content_type, "Content-Length": str(uploads.MODEL_BYTES + uploads.WRAPPER_BYTES + 1)})
        self.assertEqual(code, 413)
        self.assertNotIn("path", data["error"]["message"])
        self.assertEqual(self.request("/api/sessions", b"x" * 66000, {"Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.request("/api/files", body, {"Content-Type": content_type, "Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.service.file_store._records, {})

    def test_half_closed_request_and_duplicate_length_do_not_publish_partial(self):
        body, content_type = multipart()
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        self.addCleanup(sock.close)
        header = ("POST /api/files HTTP/1.0\r\nHost: 127.0.0.1:{0}\r\nContent-Type: {1}\r\nContent-Length: {2}\r\n\r\n".format(
            self.server.server_port, content_type, len(body) + 20)).encode()
        sock.sendall(header + body)
        sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        self.assertEqual(response.status, 400)
        response.read()
        self.assertEqual(self.service.file_store._records, {})
        sock2 = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        self.addCleanup(sock2.close)
        header = header.replace(b"\r\n\r\n", b"\r\nContent-Length: 1\r\n\r\n")
        sock2.sendall(header)
        response = http.client.HTTPResponse(sock2)
        response.begin()
        self.assertEqual(response.status, 400)
        response.read()
