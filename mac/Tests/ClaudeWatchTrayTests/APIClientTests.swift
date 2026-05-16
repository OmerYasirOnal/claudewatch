import XCTest
@testable import ClaudeWatchTray

/// URLProtocol subclass that intercepts every request and dispatches it to a
/// per-test handler. Avoids real HTTP. Custom protocols are only respected by
/// URLSessions whose configuration explicitly includes them — URLSession.shared
/// won't pick them up, so each test must build its own session.
final class MockURLProtocol: URLProtocol {
    /// A capture-friendly handler. We store it via an NSLocking-guarded ref so
    /// concurrent tests don't trip over each other (though XCTest runs them
    /// serially by default).
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = MockURLProtocol.handler else {
            fatalError("MockURLProtocol.handler not set")
        }
        do {
            let (resp, data) = try handler(request)
            client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

final class APIClientTests: XCTestCase {
    private func makeSession() -> URLSession {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [MockURLProtocol.self]
        cfg.timeoutIntervalForRequest = 2
        cfg.timeoutIntervalForResource = 2
        return URLSession(configuration: cfg)
    }

    override func tearDown() {
        MockURLProtocol.handler = nil
        super.tearDown()
    }

    func testListSessionsParses200JSON() async throws {
        let body = """
        [
          {
            "pid": 100,
            "cwd": "/Users/me/proj",
            "started_at": "2026-05-16T18:00:00Z",
            "duration_seconds": 30,
            "status": "working",
            "location_type": "iterm",
            "message_count": 2,
            "is_in_flight": true
          }
        ]
        """.data(using: .utf8)!

        MockURLProtocol.handler = { req in
            XCTAssertEqual(req.url?.path, "/api/sessions")
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, body)
        }

        let api = APIClient(session: makeSession())
        let sessions = try await api.listSessions()
        XCTAssertEqual(sessions.count, 1)
        XCTAssertEqual(sessions[0].pid, 100)
        XCTAssertEqual(sessions[0].projectName, "proj")
        XCTAssertTrue(sessions[0].isInFlight)
    }

    func testListSessionsThrowsOn500() async {
        MockURLProtocol.handler = { req in
            let resp = HTTPURLResponse(url: req.url!, statusCode: 500,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let api = APIClient(session: makeSession())
        do {
            _ = try await api.listSessions()
            XCTFail("expected APIError.http(500)")
        } catch let APIError.http(code) {
            XCTAssertEqual(code, 500)
        } catch {
            XCTFail("expected APIError.http, got \(error)")
        }
    }

    func testFocusSendsPOSTToRightURL() async throws {
        let pid = 123
        let captured = CapturedRequest()

        MockURLProtocol.handler = { req in
            captured.url = req.url
            captured.method = req.httpMethod
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let api = APIClient(session: makeSession())
        try await api.focus(pid)

        XCTAssertEqual(captured.url?.path, "/api/sessions/\(pid)/focus")
        XCTAssertEqual(captured.method, "POST")
    }

    func testPostConfigSerializesDictAsJSON() async throws {
        let captured = CapturedRequest()

        MockURLProtocol.handler = { req in
            captured.url = req.url
            captured.method = req.httpMethod
            captured.contentType = req.value(forHTTPHeaderField: "Content-Type")
            // Reading httpBody on the live URLRequest can be nil when URLSession
            // streams it; use httpBodyStream as a fallback.
            if let body = req.httpBody {
                captured.body = body
            } else if let stream = req.httpBodyStream {
                captured.body = APIClientTests.drain(stream)
            }
            let resp = HTTPURLResponse(url: req.url!, statusCode: 200,
                                       httpVersion: "HTTP/1.1", headerFields: nil)!
            return (resp, Data())
        }

        let api = APIClient(session: makeSession())
        try await api.postConfig(["plan": "pro", "port": 7799])

        XCTAssertEqual(captured.url?.path, "/api/config")
        XCTAssertEqual(captured.method, "POST")
        XCTAssertEqual(captured.contentType, "application/json")
        XCTAssertNotNil(captured.body)
        let parsed = try JSONSerialization.jsonObject(with: captured.body!) as? [String: Any]
        XCTAssertEqual(parsed?["plan"] as? String, "pro")
        XCTAssertEqual(parsed?["port"] as? Int, 7799)
    }

    // MARK: - helpers

    /// Mutable container for shared mock state.
    final class CapturedRequest: @unchecked Sendable {
        var url: URL?
        var method: String?
        var contentType: String?
        var body: Data?
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
