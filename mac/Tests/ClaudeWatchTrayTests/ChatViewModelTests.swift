import XCTest
@testable import ClaudeWatchTray

/// Pins the contract on `ChatViewModel.send()`:
///   - POSTs the trimmed text to /api/sessions/{pid}/send-text exactly once
///   - flips `isSending` true → false around the await
///   - is a no-op on empty/whitespace input
///   - surfaces backend errors via `lastError` without clearing the draft
///
/// We never touch a real Claude session — every test injects an APIClient
/// wired to a URLSession that uses `MockURLProtocol` (defined in
/// APIClientTests) instead of real HTTP.
@MainActor
final class ChatViewModelTests: XCTestCase {

    // MARK: - Fixtures

    private func makeSession() -> URLSession {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [MockURLProtocol.self]
        cfg.timeoutIntervalForRequest = 2
        cfg.timeoutIntervalForResource = 2
        return URLSession(configuration: cfg)
    }

    /// Decode a minimal Session from JSON — Session has no public memberwise
    /// init, so we go through the same Decodable path the real code uses.
    private func makeFixtureSession(pid: Int = 12345) throws -> Session {
        let json = """
        {
          "pid": \(pid),
          "cwd": "/Users/me/proj",
          "started_at": "2026-05-16T18:00:00Z",
          "duration_seconds": 30,
          "status": "working",
          "location_type": "iterm",
          "message_count": 0,
          "is_in_flight": false
        }
        """.data(using: .utf8)!
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let c = try decoder.singleValueContainer()
            let raw = try c.decode(String.self)
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = iso.date(from: raw) { return date }
            iso.formatOptions = [.withInternetDateTime]
            if let date = iso.date(from: raw) { return date }
            throw DecodingError.dataCorruptedError(
                in: c, debugDescription: "Bad date: \(raw)")
        }
        return try d.decode(Session.self, from: json)
    }

    private func makeVM(pid: Int = 12345, remote: Bool = true) throws -> ChatViewModel {
        let api = APIClient(session: makeSession())
        let vm = ChatViewModel(session: try makeFixtureSession(pid: pid), api: api)
        // The view normally toggles this from the live config; in tests we
        // skip the network and just set it directly so send() proceeds.
        vm.remoteEnabled = remote
        return vm
    }

    override func tearDown() {
        MockURLProtocol.handler = nil
        super.tearDown()
    }

    // MARK: - tests

    func testSendMessageCallsAPIWithText() async throws {
        let captured = APIClientTests.CapturedRequest()
        let pid = 12345
        MockURLProtocol.handler = { req in
            captured.url = req.url
            captured.method = req.httpMethod
            captured.contentType = req.value(forHTTPHeaderField: "Content-Type")
            if let body = req.httpBody {
                captured.body = body
            } else if let stream = req.httpBodyStream {
                captured.body = Self.drain(stream)
            }
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM(pid: pid)
        vm.draft = "hello world"

        await vm.send()

        XCTAssertEqual(captured.url?.path, "/api/sessions/\(pid)/send-text")
        XCTAssertEqual(captured.method, "POST")
        XCTAssertEqual(captured.contentType, "application/json")
        XCTAssertNotNil(captured.body)
        let parsed = try JSONSerialization.jsonObject(with: captured.body!) as? [String: Any]
        XCTAssertEqual(parsed?["text"] as? String, "hello world")
        XCTAssertEqual(parsed?["submit"] as? Bool, true)
        XCTAssertEqual(vm.draft, "", "successful send must clear the draft")
        XCTAssertNil(vm.lastError)
        XCTAssertFalse(vm.isSending, "isSending must reset before send() returns")
    }

    func testSendMessageTrimsWhitespaceBeforePost() async throws {
        let captured = APIClientTests.CapturedRequest()
        MockURLProtocol.handler = { req in
            if let body = req.httpBody {
                captured.body = body
            } else if let stream = req.httpBodyStream {
                captured.body = Self.drain(stream)
            }
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM()
        vm.draft = "   hi there\n\n"
        await vm.send()

        let parsed = try JSONSerialization.jsonObject(with: captured.body!) as? [String: Any]
        XCTAssertEqual(parsed?["text"] as? String, "hi there",
                       "leading/trailing whitespace must be stripped")
    }

    func testIsSendingFlagFlipsAroundCall() async throws {
        // We hold the mock open with a semaphore so we can observe isSending
        // from the main actor while the await is still in flight.
        let gate = TestGate()

        MockURLProtocol.handler = { req in
            // Block here until the test releases the gate. URLSession runs
            // the protocol on its own queue, so blocking is safe and lets
            // us pin the in-flight state.
            gate.wait()
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM()
        vm.draft = "ping"
        XCTAssertFalse(vm.isSending, "precondition: isSending starts false")

        // Kick off send in the background so we can observe state during it.
        let sendTask = Task { @MainActor in
            await vm.send()
        }

        // Wait for isSending to flip true. We poll briefly because the
        // assignment happens on the next main-actor hop after our Task.
        let flippedTrue = await Self.waitForCondition(timeout: 1.0) {
            await MainActor.run { vm.isSending }
        }
        XCTAssertTrue(flippedTrue, "isSending must flip true while the POST is in flight")

        // Release the mock and let send() complete.
        gate.signal()
        await sendTask.value

        XCTAssertFalse(vm.isSending, "isSending must reset to false once send() returns")
    }

    func testSendMessageDoesNotFireWhenEmpty() async throws {
        let calls = CallCounter()
        MockURLProtocol.handler = { req in
            calls.bump()
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM()

        vm.draft = ""
        await vm.send()
        vm.draft = "   "
        await vm.send()
        vm.draft = "\n\t\n"
        await vm.send()

        XCTAssertEqual(calls.count, 0,
                       "send() with empty/whitespace input must never hit the API")
        XCTAssertNil(vm.lastError)
        XCTAssertFalse(vm.isSending)
    }

    func testFailedSendSurfacesError() async throws {
        MockURLProtocol.handler = { req in
            // Pick a status the backend actually uses for send-text refusals
            // (e.g. 403 when remote_control.enabled flips off mid-flight, or
            // 429 when the rate-limiter trips). 403 keeps the assertion crisp.
            let resp = HTTPURLResponse(url: req.url!, statusCode: 403,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM()
        vm.draft = "will fail"
        await vm.send()

        XCTAssertNotNil(vm.lastError,
                        "API errors must be surfaced via lastError so the UI can show them")
        XCTAssertEqual(vm.draft, "will fail",
                       "failed send must NOT clear the draft — the user needs to retry")
        XCTAssertFalse(vm.isSending, "isSending must reset on the error path too")
    }

    func testSendShortCircuitsWhenRemoteDisabled() async throws {
        let calls = CallCounter()
        MockURLProtocol.handler = { req in
            calls.bump()
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let vm = try makeVM(remote: false)
        vm.draft = "should not send"
        await vm.send()

        XCTAssertEqual(calls.count, 0,
                       "remote_control disabled must short-circuit before any HTTP")
        XCTAssertNotNil(vm.lastError,
                        "the user gets an inline reason explaining why the send was blocked")
        XCTAssertEqual(vm.draft, "should not send")
    }

    // MARK: - helpers

    /// Polls `condition` on the main actor every 10 ms until it returns true
    /// or `timeout` elapses. Returns whether the condition was met.
    private static func waitForCondition(timeout: TimeInterval,
                                         condition: @Sendable @escaping () async -> Bool) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() { return true }
            try? await Task.sleep(nanoseconds: 10_000_000) // 10 ms
        }
        return false
    }

    private static func drain(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var out = Data()
        let bufSize = 4096
        let buf = UnsafeMutablePointer<UInt8>.allocate(capacity: bufSize)
        defer { buf.deallocate() }
        while stream.hasBytesAvailable {
            let n = stream.read(buf, maxLength: bufSize)
            if n <= 0 { break }
            out.append(buf, count: n)
        }
        return out
    }
}

/// Thread-safe call counter. The MockURLProtocol handler runs off the main
/// actor, so we need locking around mutation.
private final class CallCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var _count = 0

    func bump() {
        lock.lock(); defer { lock.unlock() }
        _count += 1
    }
    var count: Int {
        lock.lock(); defer { lock.unlock() }
        return _count
    }
}

/// Tiny semaphore wrapper. We use this to keep the mocked HTTP request open
/// long enough to observe `isSending == true` from the test side.
private final class TestGate: @unchecked Sendable {
    private let sem = DispatchSemaphore(value: 0)
    func wait() { sem.wait() }
    func signal() { sem.signal() }
}
