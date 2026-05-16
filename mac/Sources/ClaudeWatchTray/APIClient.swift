import Foundation

enum APIError: Error, CustomStringConvertible {
    case http(Int)
    case decode(Error)
    case transport(Error)

    var description: String {
        switch self {
        case .http(let code): return "HTTP \(code)"
        case .decode(let err): return "Decode: \(err)"
        case .transport(let err): return "Transport: \(err)"
        }
    }
}

/// Thin wrapper around the local claudewatch backend.
/// Bound to 127.0.0.1; the backend's TrustedHostMiddleware accepts this Host.
actor APIClient {
    static let defaultPort = 7788
    private let base: URL
    private let decoder: JSONDecoder
    private let session: URLSession

    init(port: Int = APIClient.defaultPort) {
        self.base = URL(string: "http://127.0.0.1:\(port)")!
        let d = JSONDecoder()
        // Backend emits ISO8601 with fractional seconds + 'Z' suffix.
        // Create per-call formatters inside the closure — ISO8601DateFormatter is
        // not Sendable, and capturing one would warn under Swift 6 strict mode.
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            let iso = ISO8601DateFormatter()
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = iso.date(from: raw) { return date }
            iso.formatOptions = [.withInternetDateTime]
            if let date = iso.date(from: raw) { return date }
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Bad date: \(raw)")
        }
        self.decoder = d

        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 5
        cfg.timeoutIntervalForResource = 5
        // Don't write cookies, don't keep credentials.
        cfg.httpCookieAcceptPolicy = .never
        cfg.httpShouldSetCookies = false
        self.session = URLSession(configuration: cfg)
    }

    func listSessions() async throws -> [Session] {
        return try await get("/api/sessions")
    }

    func health() async throws -> HealthReport {
        return try await get("/api/health")
    }

    func focus(_ pid: Int) async throws {
        try await post("/api/sessions/\(pid)/focus")
    }

    func halt(_ pid: Int) async throws {
        try await post("/api/sessions/\(pid)/halt")
    }

    // MARK: - internals

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let url = base.appendingPathComponent(path)
        do {
            let (data, response) = try await session.data(from: url)
            guard let http = response as? HTTPURLResponse else {
                throw APIError.http(-1)
            }
            guard (200..<300).contains(http.statusCode) else {
                throw APIError.http(http.statusCode)
            }
            do { return try decoder.decode(T.self, from: data) }
            catch { throw APIError.decode(error) }
        } catch let e as APIError { throw e }
        catch { throw APIError.transport(error) }
    }

    private func post(_ path: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        do {
            let (_, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw APIError.http(-1) }
            guard (200..<300).contains(http.statusCode) else { throw APIError.http(http.statusCode) }
        } catch let e as APIError { throw e }
        catch { throw APIError.transport(error) }
    }
}
