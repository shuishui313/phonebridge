import Foundation
import SwiftUI

@main
struct PhoneBridgeFixture: App {
    var body: some Scene {
        WindowGroup {
            VStack(spacing: 12) {
                Text("PhoneBridge").font(.title)
                Text("Simulator fixture ready")
            }
            .padding()
            .onAppear(perform: recordAppearance)
        }
    }

    private func recordAppearance() {
        let arguments = CommandLine.arguments
        guard let flag = arguments.firstIndex(of: "--phonebridge-smoke-token"),
              arguments.indices.contains(flag + 1),
              let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        else { return }

        let marker = documents.appendingPathComponent("phonebridge-smoke-token.txt")
        do {
            try arguments[flag + 1].write(to: marker, atomically: true, encoding: .utf8)
        } catch {
            print("PhoneBridge fixture could not write its smoke marker: \(error)")
        }
    }
}
